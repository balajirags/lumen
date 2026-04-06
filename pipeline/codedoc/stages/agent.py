"""Stage 2 — Reverse-engineering agent.

Uses the knowledge graph (KuzuDB) via ``codedoc.kg_tools`` and an LLM
via ``codedoc.llm`` to analyse a codebase and produce documentation artifacts.

The LLM provider is **injected** — never hardcoded.  Supports Claude, Ollama,
OpenAI, and any OpenAI-compatible endpoint.

Usage (standalone CLI)::

    python -m codedoc.stages.agent --db-path ./index.kuzu/db \\
        --provider ollama --model qwen3.5:35b --verbose

    python -m codedoc.stages.agent --db-path ./index.kuzu/db \\
        --provider claude --model claude-sonnet-4-6

Usage (as pipeline stage)::

    from codedoc.stages.agent import run_agent
    state = run_agent(state)   # PipelineState in, PipelineState out
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from codedoc.diagrams import WRITE_C4_ARTIFACT_ANTHROPIC, WRITE_C4_ARTIFACT_OPENAI, write_c4_artifact
from codedoc.kg_tools import KuzuBackend, ReverseEngineerToolkit
from codedoc.llm import LLMProvider, ToolCall, ToolDefinition, ToolParam, chat_with_retry, create_provider
from codedoc.prompts import GRAPH_CONVENTIONS_BASE

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "re-prompt.md"

_GRAPH_CONVENTIONS = GRAPH_CONVENTIONS_BASE


# ---------------------------------------------------------------------------
# write_artifact tool (agent-specific, not a graph query tool)
# ---------------------------------------------------------------------------

def _write_artifact(output_root: str, filename: str, content: str) -> str:
    """Persist a documentation artifact to disk."""
    # Strip output_root prefix if the model included a full path
    clean = filename
    for prefix in (output_root, str(Path(output_root))):
        if clean.startswith(prefix + "/"):
            clean = clean[len(prefix) + 1:]
        elif clean.startswith(prefix):
            clean = clean[len(prefix):]
    # Also strip leading slashes
    clean = clean.lstrip("/")
    dest = Path(output_root) / clean
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return f"written: {dest}"


_WRITE_ARTIFACT_DEF = ToolDefinition(
    name="write_artifact",
    description=(
        "Persist a documentation or contract artifact to disk. "
        "Subdirectory is inferred from filename."
    ),
    params=[
        ToolParam(
            name="filename",
            type="string",
            description=(
                "Relative path e.g. 'current-state/api-inventory.md', "
                "'openapi/order-service.yaml', 'manifests/artifacts.json'."
            ),
        ),
        ToolParam(
            name="content",
            type="string",
            description="Full file content to write.",
        ),
    ],
)

# Keep module-level dicts for backward compatibility (test_agent.py imports them)
_WRITE_ARTIFACT_OPENAI = _WRITE_ARTIFACT_DEF.to_openai_dict()
_WRITE_ARTIFACT_ANTHROPIC = _WRITE_ARTIFACT_DEF.to_anthropic_dict()



# ---------------------------------------------------------------------------
# System prompt loader
# ---------------------------------------------------------------------------

def _load_system_prompt(
    prompt_path: str | Path,
    db_path: str,
    repo_name: str,
    output_root: str,
) -> str:
    """Load the system prompt and inject runtime context."""
    path = Path(prompt_path)
    if path.exists():
        prompt = path.read_text(encoding="utf-8")
    else:
        prompt = "You are a code reverse-engineering agent. Use the provided tools to analyse the codebase."

    # Strip YAML frontmatter if present
    if prompt.startswith("---"):
        end = prompt.find("---", 3)
        if end != -1:
            prompt = prompt[end + 3:].lstrip()

    prompt += _GRAPH_CONVENTIONS
    prompt += (
        f"\n\n---\n"
        f"## Runtime context\n\n"
        f"- KuzuDB path: `{db_path}`\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Artifact output root: `{output_root}`\n"
        f"- Write all artifacts using the `write_artifact` tool.\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Core agentic loop helpers
# ---------------------------------------------------------------------------

def _build_assistant_message(response: Any, use_anthropic_format: bool) -> dict:
    """Build the assistant message dict for appending to message history."""
    if use_anthropic_format:
        tc_list = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in response.tool_calls
        ]
    else:
        tc_list = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in response.tool_calls
        ]
    return {"role": "assistant", "content": response.content, "tool_calls": tc_list}


def _trim_context(messages: list[dict]) -> tuple[list[dict], int]:
    """Remove the oldest assistant+tool turn group after the initial system+user messages.

    Keeps messages[0] (system) and messages[1] (user) always. Removes the first
    assistant message (with tool_calls) and all tool messages immediately following
    it. Inserts a synthetic user message noting the trimmed content.

    Returns:
        (trimmed_messages, number_of_messages_removed)
    """
    # Find the first assistant message with tool_calls at position >= 2
    trim_start = None
    trim_end = None
    for i in range(2, len(messages)):
        if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
            trim_start = i
            # Collect all following tool messages
            for j in range(i + 1, len(messages)):
                if messages[j].get("role") != "tool":
                    trim_end = j
                    break
            else:
                trim_end = len(messages)
            break

    if trim_start is None or trim_end is None or trim_end >= len(messages):
        # Nothing safe to trim
        return messages, 0

    removed = trim_end - trim_start
    placeholder = {
        "role": "user",
        "content": f"[{removed} earlier messages trimmed to stay within context limit]",
    }
    trimmed = messages[:2] + [placeholder] + messages[trim_end:]
    return trimmed, removed


def _dispatch_tool(
    tc: Any,
    toolkit: ReverseEngineerToolkit,
    output_root: str,
    artifacts: list[str],
    extra_tools: dict[str, Callable] | None,
    source_reads_remaining: list[int] | None = None,
) -> str:
    """Dispatch a single tool call and return the result text."""
    if tc.name == "write_artifact":
        result_text = _write_artifact(
            output_root,
            tc.arguments.get("filename", "unknown.md"),
            tc.arguments.get("content", ""),
        )
        if result_text.startswith("written:"):
            artifacts.append(result_text[len("written:"):].strip())
        return result_text

    # Enforce source-read budget for get_method_source
    if tc.name == "get_method_source" and source_reads_remaining is not None:
        if source_reads_remaining[0] <= 0:
            return (
                "Source-read budget exhausted. All available slots have been used. "
                "Continue analysis using graph tools only (get_class_details, execute_cypher, etc.)."
            )
        source_reads_remaining[0] -= 1

    if extra_tools and tc.name in extra_tools:
        try:
            return extra_tools[tc.name](**tc.arguments)
        except Exception as e:
            return f"Tool error: {e}"

    try:
        return toolkit.call(tc.name, **tc.arguments)
    except Exception as e:
        return f"Tool error: {e}"


# ---------------------------------------------------------------------------
# Core agentic loop
# ---------------------------------------------------------------------------

def run_loop(
    provider: LLMProvider,
    toolkit: ReverseEngineerToolkit,
    system_prompt: str,
    user_request: str,
    output_root: str,
    max_turns: int = 60,
    verbose: bool = False,
    use_anthropic_format: bool = False,
    extra_tools: dict[str, Callable] | None = None,
    extra_tool_defs: list[dict] | None = None,
    max_context_tokens: int = 120_000,
    max_source_reads: int = 15,
    include_write_artifact: bool = True,
    phase_label: str = "agent",
) -> dict[str, Any]:
    """Run the agentic tool loop.

    Args:
        provider: Injected LLM provider (Claude, Ollama, etc.).
        toolkit: Knowledge graph toolkit instance.
        system_prompt: Full system prompt text.
        user_request: The user's analysis request.
        output_root: Directory where artifacts are written.
        max_turns: Maximum LLM call turns.
        verbose: Print tool call details.
        use_anthropic_format: Use Anthropic tool definition format.
        extra_tools: Optional mapping of tool name → handler for agent-specific tools.
        extra_tool_defs: Optional additional tool definitions to include alongside graph tools.
        max_context_tokens: Trim oldest tool-result pairs when accumulated input tokens
            exceed this threshold. Set to 0 to disable context pruning.
        max_source_reads: Maximum calls to get_method_source per run. Set to 0 to disable
            source reading entirely. Default 15.
        phase_label: Prefix used in all log/print lines. Set to e.g. "phase2:inventory" so
            that parallel subagent output is distinguishable in verbose mode.

    Returns:
        Dict with status, error, artifacts, events, tool_uses.
    """
    # Source-read budget: use a mutable list so _dispatch_tool can decrement it
    source_reads_remaining: list[int] | None = [max_source_reads] if max_source_reads > 0 else None

    # Build tool definitions
    if use_anthropic_format:
        tool_defs = toolkit.anthropic_tool_definitions()
        if include_write_artifact:
            tool_defs = tool_defs + [_WRITE_ARTIFACT_ANTHROPIC]
    else:
        tool_defs = toolkit.openai_tool_definitions()
        if include_write_artifact:
            tool_defs = tool_defs + [_WRITE_ARTIFACT_OPENAI]
    if extra_tool_defs:
        tool_defs = tool_defs + extra_tool_defs

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request},
    ]
    events: list[str] = []
    artifacts: list[str] = []
    tool_uses = 0
    total_input_tokens = 0
    total_output_tokens = 0

    tag = f"[{phase_label}]"
    tool_tag = f"[{phase_label}:tool]"

    def log(msg: str) -> None:
        events.append(msg)
        if verbose:
            print(msg, flush=True)

    log(f"{tag} starting  max_turns={max_turns}")

    for turn in range(1, max_turns + 1):
        log(f"{tag} LLM call #{turn}")

        try:
            response = chat_with_retry(provider, messages, tools=tool_defs, tool_choice="auto")
        except Exception as e:
            notes = next(
                (m.get("content") or "" for m in reversed(messages)
                 if m.get("role") == "assistant" and not m.get("tool_calls")),
                ""
            )
            return {
                "status": "failed",
                "error": f"LLM API error: {e}",
                "notes": notes,
                "artifacts": artifacts,
                "events": events,
                "tool_uses": tool_uses,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            }

        total_input_tokens += response.input_tokens
        total_output_tokens += response.output_tokens

        # Always print a minimal progress line (even in non-verbose mode)
        first_tool = response.tool_calls[0].name if response.tool_calls else "stop"
        from codedoc import log as _log
        _log.print_progress_line(phase_label, turn, max_turns, first_tool)

        # No tool calls → agent is done
        if not response.tool_calls:
            log(f"{tag} model finished (stop)")
            if response.content:
                messages.append({"role": "assistant", "content": response.content})
            break

        if response.stop_reason == "max_tokens":
            log(f"{tag} WARNING: max_tokens reached, stopping")
            break

        messages.append(_build_assistant_message(response, use_anthropic_format))

        # Process all tool calls in this turn
        for tc in response.tool_calls:
            tool_uses += 1
            args_summary = json.dumps(tc.arguments)
            if len(args_summary) > 120:
                args_summary = args_summary[:120] + "…"
            log(f"{tool_tag} {tc.name}({args_summary})")

            result_text = _dispatch_tool(tc, toolkit, output_root, artifacts, extra_tools, source_reads_remaining)

            log(f"{tool_tag} → {result_text[:150]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        # Prune oldest turn group if we're over the context token budget
        if max_context_tokens > 0 and total_input_tokens > max_context_tokens:
            messages, removed = _trim_context(messages)
            if removed:
                log(f"{tag} context pruned: removed {removed} messages (input tokens so far: {total_input_tokens:,})")
    else:
        log(f"{tag} WARNING: exceeded {max_turns} tool turns — continuing with {len(artifacts)} artifact(s) produced so far")
        notes = next(
            (m.get("content") or "" for m in reversed(messages)
             if m.get("role") == "assistant" and not m.get("tool_calls")),
            ""
        )
        return {
            "status": "done",
            "error": None,
            "notes": notes,
            "artifacts": artifacts,
            "events": events,
            "tool_uses": tool_uses,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }

    log(f"{tag} tokens — input: {total_input_tokens:,}  output: {total_output_tokens:,}  total: {total_input_tokens + total_output_tokens:,}")

    notes = next(
        (m.get("content") or "" for m in reversed(messages)
         if m.get("role") == "assistant" and not m.get("tool_calls")),
        ""
    )
    return {
        "status": "done",
        "error": None,
        "notes": notes,
        "artifacts": artifacts,
        "events": events,
        "tool_uses": tool_uses,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
    }


# ---------------------------------------------------------------------------
# Supervisor: parallel Phase 2 + Phase 3, then Phase 4
# ---------------------------------------------------------------------------

_PHASE_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


_ANALYST_PROMPT_FILES: dict[str, str] = {
    "analyst/domain": "analyst-domain.md",
    "analyst/flows": "analyst-flows.md",
    "analyst/tech": "analyst-tech.md",
}

_ARCHETYPE_GUIDANCE_FILES: dict[str, str] = {
    "backend-service": "archetype-backend-service.md",
    "frontend-app": "archetype-frontend-app.md",
    "library": "archetype-library.md",
}

_ARTIFACT_CONTRACTS: dict[str, dict[str, list[str]]] = {
    "backend-service": {
        "analyst_required": [
            "domain/business-capabilities.md",
            "architecture/business-journeys.md",
            "architecture/c4-context.md",
            "tech/coupling-hotspots.md",
        ],
        "analyst_optional": [
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
        ],
        "architect_required": [
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
            "manifests/artifacts.json",
        ],
        "architect_sequence": [
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
            "manifests/artifacts.json",
        ],
        "forbidden": [],
    },
    "frontend-app": {
        "analyst_required": [
            "architecture/route-map.md",
            "architecture/component-boundaries.md",
            "architecture/user-journeys.md",
            "current-state/state-management.md",
            "current-state/data-fetching-and-api-clients.md",
            "current-state/module-dependency-map.md",
            "tech/coupling-hotspots.md",
        ],
        "analyst_optional": [],
        "architect_required": [
            "target-state/frontend-boundaries.md",
            "target-state/migration-plan.md",
            "manifests/artifacts.json",
        ],
        "architect_sequence": [
            "target-state/frontend-boundaries.md",
            "target-state/migration-plan.md",
            "manifests/artifacts.json",
        ],
        "forbidden": [
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
        ],
    },
    "library": {
        "analyst_required": [
            "architecture/public-surface.md",
            "current-state/core-abstractions.md",
            "current-state/extension-points.md",
            "current-state/module-structure.md",
            "current-state/dependency-map.md",
            "tech/coupling-hotspots.md",
        ],
        "analyst_optional": [],
        "architect_required": [
            "target-state/api-evolution.md",
            "target-state/refactoring-seams.md",
            "target-state/migration-guidance.md",
            "manifests/artifacts.json",
        ],
        "architect_sequence": [
            "target-state/api-evolution.md",
            "target-state/refactoring-seams.md",
            "target-state/migration-guidance.md",
            "manifests/artifacts.json",
        ],
        "forbidden": [
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
        ],
    },
}


def _artifact_contract(archetype: str) -> dict[str, list[str]]:
    return _ARTIFACT_CONTRACTS.get(archetype, _ARTIFACT_CONTRACTS["backend-service"])


def _required_artifacts(archetype: str) -> list[str]:
    contract = _artifact_contract(archetype)
    return contract["analyst_required"] + contract["architect_required"]


def _current_state_artifacts(archetype: str) -> list[str]:
    contract = _artifact_contract(archetype)
    return contract["analyst_required"] + contract["analyst_optional"]


def _architect_sequence(archetype: str) -> list[str]:
    return _artifact_contract(archetype)["architect_sequence"]


def _write_machine_manifest(artifacts_dir: str, repo_name: str, archetype: str) -> str:
    root = Path(artifacts_dir)
    manifest_path = root / "manifests" / "artifacts.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    for rel_path in _required_artifacts(archetype) + _artifact_contract(archetype)["analyst_optional"]:
        full_path = root / rel_path
        if full_path.exists():
            files.append(rel_path)
    files = sorted(dict.fromkeys(files), key=lambda path: (0 if path.startswith("target-state/") is False else 1, path))

    entries: list[dict[str, Any]] = []
    for rel_path in files:
        phase = 3 if rel_path.startswith("target-state/") or rel_path == "manifests/artifacts.json" else 2
        evidence = "Prescriptive" if rel_path.startswith("target-state/") else "Observed"
        entries.append({"file": rel_path, "phase": phase, "evidence": evidence})

    omitted: list[dict[str, str]] = []
    for rel_path in _artifact_contract(archetype)["analyst_optional"]:
        if not (root / rel_path).exists():
            omitted.append({"file": rel_path, "reason": "Optional artifact not produced from available graph evidence."})

    manifest = {
        "version": "1.0",
        "repo_name": repo_name,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "artifacts": entries,
        "omitted": omitted,
        "archetype": archetype,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return str(manifest_path)


def _load_prompt_file(path: Path, fallback: str) -> str:
    prompt = path.read_text(encoding="utf-8") if path.exists() else fallback
    if prompt.startswith("---"):
        end = prompt.find("---", 3)
        if end != -1:
            prompt = prompt[end + 3:].lstrip()
    return prompt


def _load_archetype_guidance(archetype: str) -> str:
    fname = _ARCHETYPE_GUIDANCE_FILES.get(archetype)
    if not fname:
        return ""
    path = _PHASE_PROMPTS_DIR / fname
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _select_repo_archetype(backend: KuzuBackend) -> str:
    def _count(query: str) -> int:
        try:
            rows = backend.execute(query)
            return int(rows[0]["c"]) if rows else 0
        except Exception:
            return 0

    component_count = _count("MATCH (n:Component) RETURN count(n) AS c")
    hook_count = _count("MATCH (n:Hook) RETURN count(n) AS c")
    render_count = _count("MATCH ()-[r:RENDERS]->() RETURN count(r) AS c")
    api_annotation_count = _count(
        "MATCH (n)-[:HAS_ANNOTATION]->(a:AnnotationType) "
        "WHERE a.name =~ '(?i).*(RestController|Controller|RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|Path|Route|Router|FastAPI|Blueprint).*' "
        "RETURN count(DISTINCT n) AS c"
    )
    route_like_count = _count(
        "MATCH (n) WHERE n.name =~ '(?i).*(route|endpoint|handler|controller|api|get_|post_|put_|delete_).*' "
        "RETURN count(DISTINCT n) AS c"
    )
    method_count = _count("MATCH (n:Method) RETURN count(n) AS c")
    function_count = _count("MATCH (n:Function) RETURN count(n) AS c")
    package_count = _count("MATCH (n:Package) RETURN count(n) AS c")

    frontend_score = (component_count * 3) + (hook_count * 2) + render_count
    backend_score = (api_annotation_count * 3) + route_like_count + method_count + package_count

    if frontend_score >= 3 and frontend_score >= backend_score:
        return "frontend-app"
    if backend_score >= 3:
        return "backend-service"
    if function_count > 0 or method_count > 0:
        return "library"
    return "backend-service"


def _build_analyst_requests(archetype: str) -> dict[str, str]:
    if archetype == "frontend-app":
        return {
            "analyst/domain": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_schema, get_route_map, get_state_management_summary.\n"
                "TURN 2 — batch in one response: get_component_boundary_map, get_entry_points, get_external_dependencies.\n"
                "TURN 3 — call write_artifact('architecture/route-map.md', content). "
                "Content: route/screen table, owners, entry components, and notable guarded routes grounded in graph evidence.\n"
                "TURN 4 — call write_artifact('current-state/state-management.md', content). "
                "Content: stores, contexts, hooks, async state boundaries, cache/query layers, and ownership notes. "
                "Do NOT write 'likely state variables' lists or speculative shared-state claims unless directly evidenced.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/flows": (
                "Execute in 5 turns.\n"
                "TURN 1 — batch in one response: get_route_map, get_api_client_summary, get_external_dependencies, get_entry_points.\n"
                "TURN 2 — call trace_user_flow on the 3 highest-signal user journeys from Turn 1. "
                "Prefer routes, screens, actions, or top-level components.\n"
                "TURN 3 — call write_artifact('architecture/user-journeys.md', content). "
                "Document 3-5 user journeys grounded in routes/components. If you include diagrams, fence them as ```mermaid blocks.\n"
                "TURN 4 — call write_artifact('current-state/data-fetching-and-api-clients.md', content). "
                "Content: API clients, async data flow, and integration boundaries. "
                "Do NOT invent auth headers/tokens, retries, polling services, external validation services, or initialization steps without direct evidence.\n"
                "TURN 5 — call write_artifact('architecture/component-boundaries.md', content). "
                "Content: top-level screens, shared layout, reusable UI, feature modules, and cross-feature coupling.\n"
                "Do NOT call get_method_source. Do NOT write backend-only artifacts. Stop after Turn 5."
            ),
            "analyst/tech": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_module_dependency_map, get_component_coupling_matrix, get_hotspots(coupling), "
                "detect_circular_dependencies, get_unused_code.\n"
                "TURN 2 — call impact_analysis on the top 3 modules/components from Turn 1.\n"
                "TURN 3 — call write_artifact('current-state/module-dependency-map.md', content). "
                "Content: feature/module dependency summary, shared utilities, cycles, and extraction seams.\n"
                "TURN 4 — call write_artifact('tech/coupling-hotspots.md', content). "
                "Content: hotspot table, cycles, dead code, and migration impact on modules/components.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
        }

    if archetype == "library":
        return {
            "analyst/domain": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_schema, get_public_api_surface, get_extension_points.\n"
                "TURN 2 — batch in one response: get_design_patterns, get_architecture_overview, get_entry_points.\n"
                "TURN 3 — call write_artifact('architecture/public-surface.md', content). "
                "Content: exported/public APIs, important modules, expected consumers, and compatibility-sensitive areas.\n"
                "TURN 4 — call write_artifact('current-state/core-abstractions.md', content). "
                "Content: core abstractions, key types/interfaces, invariants, and responsibility boundaries.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/flows": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_public_api_surface, get_extension_points, get_external_dependencies, get_architecture_overview.\n"
                "TURN 2 — call trace_user_flow on the 3 most important public APIs or extension seams from Turn 1.\n"
                "TURN 3 — call write_artifact('current-state/extension-points.md', content). "
                "Content: plugin hooks, callbacks, interfaces, subclass points, configuration seams, and lifecycle expectations.\n"
                "TURN 4 — call write_artifact('current-state/module-structure.md', content). "
                "Content: package/module layout, responsibilities, and consumer usage flows rather than HTTP journeys.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/tech": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_module_dependency_map, get_component_coupling_matrix, get_hotspots(coupling), "
                "detect_circular_dependencies, get_unused_code.\n"
                "TURN 2 — call impact_analysis on the top 3 modules/packages from Turn 1.\n"
                "TURN 3 — call write_artifact('current-state/dependency-map.md', content). "
                "Content: internal package dependencies, external dependencies, layering problems, and refactoring seams.\n"
                "TURN 4 — call write_artifact('tech/coupling-hotspots.md', content). "
                "Content: hotspot table, cycles, dead code, and API stability risks.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
        }

    domain_turn4 = (
        "TURN 4 — call write_artifact('domain/er-diagram.md', content) if persistent entities "
        "were found in Turns 1-2 (look for @Entity, repositories, ORM annotations). "
        "Content: Mermaid erDiagram + bounded context ownership table. "
        "If no persistent entities found, write a one-line file: "
        "'_No persistent entities found in graph._'\n"
    )
    flows_turn2 = (
        "TURN 2 — call trace_user_flow on the 3 most important mutation entry points "
        "(POST/PUT/PATCH/DELETE) from Turn 1.\n"
    )
    flows_turn3 = (
        "TURN 3 — call write_artifact('architecture/business-journeys.md', content). "
        "3-5 flows each with: '**Business journey:** As a [role], I can [action] by calling "
        "[METHOD /path].' followed by a Mermaid sequence diagram fenced as ```mermaid. "
        "Use sequenceDiagram syntax with participants and arrows.\n"
    )
    tech_turn3 = (
        "TURN 3 — call write_artifact('tech/coupling-hotspots.md', content). "
        "Content: hotspot table (component | type | score | migration impact), "
        "top-5 coupling pairs, dead code top-10, decomposition signals "
        "(packages with low coupling = good extraction candidates).\n"
    )

    return {
        "analyst/domain": (
            "Execute in 4 turns.\n"
            "TURN 1 — batch in one response: get_schema, get_domain_model, get_annotations_usage.\n"
            "TURN 2 — batch in one response: get_class_details on the 5 most important entity classes "
            "found in Turn 1 (prefer @Entity annotated or aggregate root classes); "
            "execute_cypher: MATCH (n) WHERE n.name =~ "
            "'(?i).*(Event|Command|Created|Confirmed|Cancelled|Published|Topic).*' "
            "RETURN label(n) AS type, n.name AS name LIMIT 30.\n"
            "TURN 3 — call write_artifact('domain/business-capabilities.md', content). "
            "One section per capability: name in business terms, core operations (bullets), "
            "business rules/validations in business language with evidence citations, key entities.\n"
            + domain_turn4 +
            "Do NOT call get_method_source. Stop after Turn 4."
        ),
        "analyst/flows": (
            "Execute in 5 turns.\n"
            "TURN 1 — batch in one response: get_entry_points, get_api_endpoints, "
            "get_external_dependencies, "
            "execute_cypher: MATCH (n) WHERE n.name =~ "
            "'(?i).*(Client|Producer|Consumer|Gateway|Adapter|Listener|Sender|Subscriber).*' "
            "RETURN label(n) AS type, n.name AS name LIMIT 40.\n"
            + flows_turn2 +
            flows_turn3 +
            "TURN 4 — call write_c4_artifact('architecture/c4-context.md', title, summary, spec_json). "
            "Use structured data only. spec_json must contain people, systems, external_systems, and relations arrays. "
            "external_systems kinds: system, database, queue. relations use from, to, label, technology, bidirectional.\n"
            "TURN 5 — call write_artifact('current-state/api-spec.yaml', content) ONLY if Turn 1 "
            "found HTTP endpoints with clear path + method signatures. Skip this turn otherwise.\n"
            "Do NOT call get_method_source. Stop after Turn 5 (or 4 if skipping api-spec)."
        ),
        "analyst/tech": (
            "Execute in 3 turns.\n"
            "TURN 1 — batch in one response: get_hotspots(coupling), get_hotspots(fan_in), "
            "get_hotspots(fan_out), get_hotspots(god_class), get_component_coupling_matrix, "
            "detect_circular_dependencies, get_unused_code, get_design_patterns.\n"
            "TURN 2 — call impact_analysis on the top 3 hotspot components from Turn 1.\n"
            + tech_turn3 +
            "Do NOT call get_method_source. Stop after Turn 3."
        ),
}


def validate_artifacts(
    artifacts_dir: str,
    required_artifacts: list[str] | None = None,
    archetype: str = "backend-service",
) -> list[str]:
    required = required_artifacts or _required_artifacts(archetype)
    return [rel_path for rel_path in required if not (Path(artifacts_dir) / rel_path).exists()]


def validate_artifact_quality(artifacts_dir: str, archetype: str) -> list[str]:
    issues: list[str] = []
    root = Path(artifacts_dir)
    contract = _artifact_contract(archetype)

    for rel_path in contract["forbidden"]:
        if (root / rel_path).exists():
            issues.append(f"unexpected artifact for {archetype}: {rel_path}")

    api_spec = root / "current-state" / "api-spec.yaml"
    if api_spec.exists():
        content = api_spec.read_text(encoding="utf-8").lower()
        if "paths:" not in content or re.search(r"paths:\s*(\{\s*\}|$)", content):
            issues.append("current-state/api-spec.yaml: empty or malformed paths section")
        if "no rest endpoints" in content or "no http endpoints" in content:
            issues.append("current-state/api-spec.yaml: contradictory empty-endpoint narrative")

    if archetype == "frontend-app":
        for rel_path in ["target-state/frontend-boundaries.md", "target-state/migration-plan.md"]:
            full_path = root / rel_path
            if not full_path.exists():
                continue
            content = full_path.read_text(encoding="utf-8").lower()
            if re.search(r"\b(kafka|postgres|postgresql|cdc|anti-corruption layer|acl|bounded context|microservice)\b", content):
                issues.append(f"{rel_path}: frontend target state introduces backend-only concepts")

    return issues


def validate_artifact_warnings(artifacts_dir: str) -> list[str]:
    warnings: list[str] = []
    root = Path(artifacts_dir)

    for md_file in root.rglob("*.md"):
        rel_path = str(md_file.relative_to(root))
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "@startuml" in content and "```plantuml" not in content:
            warnings.append(f"{rel_path}: PlantUML blocks should be fenced with ```plantuml")

    return warnings


def _build_analyst_system_prompt(
    analyst_name: str,
    db_path: str,
    repo_name: str,
    orientation_summary: str,
    archetype: str,
    repo_metrics: dict[str, Any] | None = None,
) -> str:
    """Build the system prompt for an analyst agent (graph tools + write_artifact)."""
    fname = _ANALYST_PROMPT_FILES.get(analyst_name, "analyst-domain.md")
    prompt_path = _PHASE_PROMPTS_DIR / fname
    prompt = _load_prompt_file(prompt_path, (
        "You are a code analyst. Query the knowledge graph and write artifacts using write_artifact."
    ))
    guidance = _load_archetype_guidance(archetype)
    if guidance:
        prompt += f"\n\n---\n## Repo Archetype Guidance\n\n{guidance}\n"
    if repo_metrics:
        total_files = int(repo_metrics.get("total_source_files", 0) or 0)
        total_loc = int(repo_metrics.get("total_loc", 0) or 0)
        if total_files <= 20 or total_loc <= 5_000:
            prompt += (
                "\n\n---\n## Repo Size Guidance\n\n"
                "This is a small repo. Prefer minimal, proportionate conclusions. "
                "Do not invent enterprise layers, shared platforms, or large-scale restructures unless the graph shows them clearly.\n"
            )
    prompt += f"\n\n---\n## Orientation Summary\n\n{orientation_summary}\n"
    prompt += _GRAPH_CONVENTIONS
    required_artifacts = "\n".join(f"  - `{path}`" for path in _artifact_contract(archetype)["analyst_required"])
    prompt += (
        f"\n\n---\n## Runtime context\n\n"
        f"- KuzuDB path: `{db_path}`\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Repo archetype: `{archetype}`\n"
        f"- Artifact output root: see write_artifact tool\n"
        f"- Use write_artifact to write each artifact to disk.\n"
        f"- Required current-state artifacts for this repo archetype:\n{required_artifacts}\n"
        f"- If you emit PlantUML, fence every diagram as ` ```plantuml ` with `@startuml` / `@enduml`.\n"
        f"- Treat current-state artifacts as evidence documents: do not invent services, retries, auth flows, initialization steps, polling systems, or external integrations unless they are directly supported by graph evidence.\n"
        f"- Prefer `[Observed]`, `[Inferred]`, and `[Unknown]` correctly; weak evidence must not be written as `[Observed]`.\n"
        f"- Do NOT call get_method_source.\n"
    )
    return prompt


def _build_architect_prompt(
    orientation_summary: str,
    artifacts_dir: str,
    repo_name: str,
    archetype: str,
    repo_metrics: dict[str, Any] | None = None,
) -> str:
    """Build the architect system prompt, injecting Phase 2 artifact contents."""
    prompt_path = _PHASE_PROMPTS_DIR / "architect.md"
    prompt = _load_prompt_file(prompt_path, (
        "You are a Solution Architect. Write target-state artifacts using write_artifact only. "
        "Do NOT call graph query tools."
    ))
    guidance = _load_archetype_guidance(archetype)
    if guidance:
        prompt += f"\n\n---\n## Repo Archetype Guidance\n\n{guidance}\n"
    if repo_metrics:
        total_files = int(repo_metrics.get("total_source_files", 0) or 0)
        total_loc = int(repo_metrics.get("total_loc", 0) or 0)
        if total_files <= 20 or total_loc <= 5_000:
            prompt += (
                "\n\n---\n## Repo Size Guidance\n\n"
                "This is a small repo. Keep target-state recommendations proportionate. "
                "Prefer light refactors over introducing routers, stores, layers, or platform abstractions unless current-state evidence strongly justifies them.\n"
            )

    prompt += f"\n\n---\n## Orientation Summary\n\n{orientation_summary}\n"

    # Inject current-state artifacts written by analysts
    ARTIFACT_CHAR_LIMIT = 10_000
    contract = _artifact_contract(archetype)
    current_state_files = _current_state_artifacts(archetype)
    prompt += "\n\n---\n## Current State Artifacts (written by analysts)\n\n"
    for rel_path in current_state_files:
        full_path = Path(artifacts_dir) / rel_path
        if full_path.exists():
            content = full_path.read_text(encoding="utf-8")
            if len(content) > ARTIFACT_CHAR_LIMIT:
                content = content[:ARTIFACT_CHAR_LIMIT] + f"\n\n[... truncated at {ARTIFACT_CHAR_LIMIT:,} chars]"
            prompt += f"### {rel_path}\n\n{content}\n\n"
        else:
            prompt += f"### {rel_path}\n\n_Not produced by analyst._\n\n"

    target_sequence = " → ".join(_architect_sequence(archetype))
    forbidden = contract["forbidden"]
    forbidden_text = ", ".join(f"`{path}`" for path in forbidden) if forbidden else "_None_"

    prompt += (
        f"\n\n---\n## Runtime context\n\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Repo archetype: `{archetype}`\n"
        f"- Artifact output root: see write_artifact tool\n"
        f"- Write target-state artifacts using the `write_artifact` tool.\n"
        f"- `manifests/artifacts.json` is generated by the pipeline. Do not write it yourself.\n"
        f"- Do NOT call any graph query tools.\n"
        f"- CRITICAL: Write the required target-state artifacts in order: {target_sequence}. "
        f"Do not rename them.\n"
        f"- Forbidden artifacts for this repo archetype: {forbidden_text}\n"
        f"- If you emit PlantUML, fence every diagram as ` ```plantuml ` with `@startuml` / `@enduml`.\n"
        f"- Every recommendation must stay native to the selected archetype; do not invent backend services for frontend repos or HTTP/service plans for libraries without clear evidence.\n"
        f"- Stop only after all required target-state artifacts have been written.\n"
    )
    return prompt


def _build_architect_request(archetype: str) -> str:
    if archetype == "frontend-app":
        return (
            "Write the 2 target-state artifacts in order:\n"
            "TURN 1: write_artifact('target-state/frontend-boundaries.md', ...)\n"
            "TURN 2: write_artifact('target-state/migration-plan.md', ...)\n"
            "Do NOT write manifests/artifacts.json; the pipeline will generate it.\n"
            "Do NOT call any graph query tools. Do NOT write backend-only artifacts. Stop after Turn 2."
        )

    if archetype == "library":
        return (
            "Write the 3 target-state artifacts in order:\n"
            "TURN 1: write_artifact('target-state/api-evolution.md', ...)\n"
            "TURN 2: write_artifact('target-state/refactoring-seams.md', ...)\n"
            "TURN 3: write_artifact('target-state/migration-guidance.md', ...)\n"
            "Do NOT write manifests/artifacts.json; the pipeline will generate it.\n"
            "Do NOT call any graph query tools. Do NOT write backend-service migration plans. Stop after Turn 3."
        )

    return (
        "Write the 2 target-state artifacts in order:\n"
        "TURN 1: write_artifact('target-state/bounded-contexts.md', ...)\n"
        "TURN 2: write_artifact('target-state/strangler-fig.md', ...)\n"
        "Do NOT write manifests/artifacts.json; the pipeline will generate it.\n"
        "Do NOT call any graph query tools. Stop after Turn 2."
    )


def _missing_target_state_artifacts(artifacts_dir: str, archetype: str) -> list[str]:
    contract = _artifact_contract(archetype)
    missing: list[str] = []
    for rel_path in contract["architect_sequence"]:
        if not rel_path.startswith("target-state/"):
            continue
        if not (Path(artifacts_dir) / rel_path).exists():
            missing.append(rel_path)
    return missing


def _backfill_required_artifacts(kuzu_path: str, repo_path: str, artifacts_dir: str, archetype: str) -> list[str]:
    generated: list[str] = []
    if archetype != "frontend-app":
        return generated

    target = Path(artifacts_dir) / "current-state" / "module-dependency-map.md"
    if target.exists():
        return generated

    backend = KuzuBackend(kuzu_path)
    toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
    summary = toolkit.call("get_module_dependency_map")
    content = (
        "# Module Dependency Map\n\n"
        "## Observed Dependency Summary [Observed]\n\n"
        "The following summary was generated directly from the knowledge graph.\n\n"
        "```text\n"
        f"{summary}\n"
        "```\n"
    )
    _write_artifact(artifacts_dir, "current-state/module-dependency-map.md", content)
    generated.append(str(target))
    return generated


def run_supervisor_agent(
    provider: LLMProvider,
    kuzu_path: str,
    repo_path: str,
    repo_name: str,
    artifacts_dir: str,
    base_prompt_path: str | Path,
    max_turns: int = 120,
    verbose: bool = False,
    use_anthropic_format: bool = False,
    max_context_tokens: int = 120_000,
    max_source_reads: int = 15,
    repo_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the Analyst + Architect pattern.

    Phase 1:   direct toolkit query (no LLM tokens) — orientation summary.
    Phase 2:   3 parallel Analyst agents — each has graph tools + write_artifact:
               - analyst/domain  (Business Analyst)    → business-capabilities.md + er-diagram.md
               - analyst/flows   (Integration Architect)→ business-journeys.md + c4-context.md + [api-spec]
               - analyst/tech    (Staff Engineer)       → coupling-hotspots.md
    Phase 3:   1 Architect agent — reads Phase 2 artifacts, writes target-state + manifest.

    Returns the same dict shape as run_loop().
    """
    all_events: list[str] = []
    all_artifacts: list[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_tool_uses = 0
    repo_archetype = "backend-service"

    def log(msg: str) -> None:
        all_events.append(msg)
        from codedoc import log as _log
        _log.print_supervisor_line(msg)

    # ------------------------------------------------------------------
    # Phase 1: direct toolkit query — no LLM needed
    # ------------------------------------------------------------------
    log("[supervisor] Phase 1 — orientation (direct toolkit query)")
    try:
        p1_backend = KuzuBackend(kuzu_path)
        p1_toolkit = ReverseEngineerToolkit(p1_backend, repo_path=repo_path)
        orientation_summary = p1_toolkit.call("get_architecture_summary")
        repo_archetype = _select_repo_archetype(p1_backend)
        log(f"[supervisor] Orientation complete ({len(orientation_summary):,} chars)")
        log(f"[supervisor] Repo archetype selected: {repo_archetype}")
    except Exception as e:
        return {
            "status": "failed",
            "error": f"Phase 1 orientation failed: {e}",
            "notes": "",
            "artifacts": all_artifacts,
            "events": all_events,
            "tool_uses": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_archetype": repo_archetype,
        }

    # ------------------------------------------------------------------
    # Phase 2: 3 parallel Analysts — graph tools + write_artifact
    # ------------------------------------------------------------------
    analyst_turns = max(10, max_turns // 5)    # 24 turns for max_turns=120
    architect_turns = max(10, max_turns // 4)  # 30 turns for max_turns=120

    analyst_requests = _build_analyst_requests(repo_archetype)

    def _run_analyst(name: str, user_request: str) -> tuple[str, dict]:
        backend = KuzuBackend(kuzu_path)
        toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
        analyst_system = _build_analyst_system_prompt(
            name, kuzu_path, repo_name, orientation_summary, repo_archetype, repo_metrics=repo_metrics
        )
        result = run_loop(
            provider=provider,
            toolkit=toolkit,
            system_prompt=analyst_system,
            user_request=user_request,
            output_root=artifacts_dir,
            max_turns=analyst_turns,
            verbose=verbose,
            use_anthropic_format=use_anthropic_format,
            max_context_tokens=max_context_tokens,
            max_source_reads=0,
            include_write_artifact=True,
            extra_tools={"write_c4_artifact": lambda **kwargs: write_c4_artifact(artifacts_dir, **kwargs)},
            extra_tool_defs=[WRITE_C4_ARTIFACT_ANTHROPIC if use_anthropic_format else WRITE_C4_ARTIFACT_OPENAI],
            phase_label=name,
        )
        return name, result

    log(f"[supervisor] Phase 2 — spawning {len(analyst_requests)} analysts in parallel (domain · flows · tech)…")

    from codedoc import log as _log
    _log.start_agent_boxes()
    try:
        with ThreadPoolExecutor(max_workers=len(analyst_requests)) as pool:
            futures = {
                pool.submit(_run_analyst, name, req): name
                for name, req in analyst_requests.items()
            }
            for name in analyst_requests:
                _log.update_agent_box(name, status="running", tool="starting")
            for future in as_completed(futures):
                name, result = future.result()
                all_events.extend(result["events"])
                total_input_tokens += result["input_tokens"]
                total_output_tokens += result["output_tokens"]
                total_tool_uses += result["tool_uses"]
                n_artifacts = len(result["artifacts"])
                if result["status"] == "failed":
                    _log.update_agent_box(name, status="failed", tool="error")
                    log(f"[supervisor] WARNING: {name} failed: {result['error']}")
                else:
                    all_artifacts.extend(result["artifacts"])
                    _log.update_agent_box(name, status="done", tool="complete", artifacts=n_artifacts)
                    log(f"[supervisor] {name} done — {n_artifacts} artifact(s)")
                _log.print_researcher_done(name, n_artifacts)
    finally:
        _log.stop_agent_boxes()
    # ------------------------------------------------------------------
    # Phase 3: Architect — reads Phase 2 artifacts, writes target-state
    # ------------------------------------------------------------------
    log("[supervisor] Phase 3 — running architect…")
    backfilled = _backfill_required_artifacts(kuzu_path, repo_path, artifacts_dir, repo_archetype)
    if backfilled:
        all_artifacts.extend(backfilled)
        for path in backfilled:
            all_events.append(f"[supervisor] backfilled artifact — {path}")
    arch_system = _build_architect_prompt(
        orientation_summary, artifacts_dir, repo_name, repo_archetype, repo_metrics=repo_metrics
    )
    arch_backend = KuzuBackend(kuzu_path)
    arch_toolkit = ReverseEngineerToolkit(arch_backend, repo_path=repo_path)
    arch_result = run_loop(
        provider=provider,
        toolkit=arch_toolkit,
        system_prompt=arch_system,
        user_request=_build_architect_request(repo_archetype),
        output_root=artifacts_dir,
        max_turns=architect_turns,
        verbose=verbose,
        use_anthropic_format=use_anthropic_format,
        max_context_tokens=max_context_tokens,
        max_source_reads=0,
        include_write_artifact=True,
        phase_label="architect",
    )

    missing_target_artifacts = _missing_target_state_artifacts(artifacts_dir, repo_archetype)
    if arch_result["status"] != "failed" and missing_target_artifacts:
        recovery_request = (
            "You stopped before writing the required target-state artifacts.\n"
            f"Write ONLY these missing artifacts now, in order: {', '.join(missing_target_artifacts)}.\n"
            "Use write_artifact for each listed file.\n"
            "Do NOT call graph query tools. Do NOT write manifests/artifacts.json.\n"
        )
        all_events.append(
            "[supervisor] architect recovery — missing target-state artifacts: "
            + ", ".join(missing_target_artifacts)
        )
        recovery_result = run_loop(
            provider=provider,
            toolkit=arch_toolkit,
            system_prompt=arch_system,
            user_request=recovery_request,
            output_root=artifacts_dir,
            max_turns=max(6, architect_turns // 2),
            verbose=verbose,
            use_anthropic_format=use_anthropic_format,
            max_context_tokens=max_context_tokens,
            max_source_reads=0,
            include_write_artifact=True,
            phase_label="architect/recovery",
        )
        arch_result["events"].extend(recovery_result["events"])
        arch_result["artifacts"].extend(recovery_result["artifacts"])
        arch_result["tool_uses"] += recovery_result["tool_uses"]
        arch_result["input_tokens"] += recovery_result["input_tokens"]
        arch_result["output_tokens"] += recovery_result["output_tokens"]
        if recovery_result["status"] == "failed":
            arch_result["status"] = "failed"
            arch_result["error"] = recovery_result["error"]

    all_events.extend(arch_result["events"])
    all_artifacts.extend(arch_result["artifacts"])
    total_input_tokens += arch_result["input_tokens"]
    total_output_tokens += arch_result["output_tokens"]
    total_tool_uses += arch_result["tool_uses"]
    all_events.append(f"[supervisor] architect done — {len(arch_result['artifacts'])} artifact(s)")
    from codedoc import log as _log
    _log.print_synthesizer_done(len(arch_result["artifacts"]))

    if arch_result["status"] == "failed":
        return {
            "status": "failed",
            "error": f"architect failed: {arch_result['error']}",
            "notes": "",
            "artifacts": all_artifacts,
            "events": all_events,
            "tool_uses": total_tool_uses,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "prompt_archetype": repo_archetype,
        }

    manifest_path = _write_machine_manifest(artifacts_dir, repo_name, repo_archetype)
    all_artifacts.append(manifest_path)
    all_events.append(f"[supervisor] manifest generated — {manifest_path}")

    _summary = (
        f"[supervisor] all phases complete — "
        f"artifacts: {len(all_artifacts)}  "
        f"tokens: {total_input_tokens:,} in / {total_output_tokens:,} out  "
        f"tool uses: {total_tool_uses}"
    )
    all_events.append(_summary)
    from codedoc import log as _log
    _log.print_supervisor_summary(len(all_artifacts), total_input_tokens, total_output_tokens, total_tool_uses)

    return {
        "status": "done",
        "error": None,
        "notes": "",
        "artifacts": all_artifacts,
        "events": all_events,
        "tool_uses": total_tool_uses,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "prompt_archetype": repo_archetype,
    }


# ---------------------------------------------------------------------------
# Pipeline stage 2
# ---------------------------------------------------------------------------

def run_agent(state) -> Any:
    """Pipeline stage 2. Accepts PipelineState, returns PipelineState.

    Reads: kuzu_path, repo_path, output_dir, model, provider, base_url,
           agent_prompt, max_turns, verbose.
    Writes: artifacts_dir, events, input_tokens, output_tokens, tool_uses.
    """
    from codedoc.state import PipelineState

    # Support both PipelineState objects and dicts
    if isinstance(state, dict):
        ps = state.get("pipeline", state)
        is_dict = not isinstance(ps, PipelineState)
    else:
        ps = state
        is_dict = False

    kuzu_path = ps.kuzu_path if not is_dict else ps.get("kuzu_path", "")
    output_dir = ps.output_dir if not is_dict else ps.get("output_dir", "./output")
    model = ps.model if not is_dict else ps.get("model", "claude-sonnet-4-6")
    provider_name = ps.provider if not is_dict else ps.get("provider", "auto")
    base_url = ps.base_url if not is_dict else ps.get("base_url", "")
    prompt_path = ps.agent_prompt if not is_dict else ps.get("agent_prompt", "")
    max_turns = ps.max_turns if not is_dict else ps.get("max_turns", 60)
    max_context_tokens = ps.max_context_tokens if not is_dict else ps.get("max_context_tokens", 120_000)
    verbose = ps.verbose if not is_dict else ps.get("verbose", False)
    repo_path = ps.repo_path if not is_dict else ps.get("repo_path", "")

    repo_name = Path(repo_path).name if repo_path else (Path(kuzu_path).parent.name if kuzu_path else "unknown")

    events = list(ps.events) if hasattr(ps, "events") else []

    # Quick connectivity check before spinning up the supervisor
    try:
        KuzuBackend(kuzu_path)
    except FileNotFoundError as e:
        if not is_dict:
            ps.status = "failed"
            ps.error = str(e)
        return state

    provider = create_provider(provider=provider_name, model=model, base_url=base_url)

    use_anthropic = provider_name in ("claude", "anthropic") or (
        provider_name == "auto" and (model.startswith("claude") or model.startswith("anthropic"))
    )

    artifacts_dir = str(Path(output_dir) / "artifacts")
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    resolved_prompt = prompt_path or str(_PROMPT_PATH)

    result = run_supervisor_agent(
        provider=provider,
        kuzu_path=kuzu_path,
        repo_path=repo_path,
        repo_name=repo_name,
        artifacts_dir=artifacts_dir,
        base_prompt_path=resolved_prompt,
        max_turns=max_turns,
        verbose=verbose,
        use_anthropic_format=use_anthropic,
        max_context_tokens=max_context_tokens,
        repo_metrics=ps.repo_metrics if not is_dict else ps.get("repo_metrics"),
    )

    events.extend(result["events"])

    if not is_dict:
        ps.events = events
        ps.input_tokens = result["input_tokens"]
        ps.output_tokens = result["output_tokens"]
        ps.tool_uses = result["tool_uses"]
        ps.prompt_archetype = result.get("prompt_archetype", "")
        if result["status"] == "failed":
            ps.status = "failed"
            ps.error = result["error"]
        else:
            ps.artifacts_dir = artifacts_dir
            if not result["artifacts"]:
                ps.status = "failed"
                ps.error = "agent completed but wrote no artifacts"
            else:
                missing_artifacts = validate_artifacts(artifacts_dir, archetype=ps.prompt_archetype or "backend-service")
                quality_issues = validate_artifact_quality(artifacts_dir, ps.prompt_archetype or "backend-service")
                quality_warnings = validate_artifact_warnings(artifacts_dir)
                if missing_artifacts:
                    ps.status = "failed"
                    ps.error = "agent completed but missed mandatory artifacts: " + ", ".join(missing_artifacts)
                elif quality_issues:
                    ps.status = "failed"
                    ps.error = "agent output failed validation: " + "; ".join(quality_issues)
                else:
                    for warning in quality_warnings:
                        ps.log("agent", f"warning: {warning}")
                    ps.log("agent", f"{len(result['artifacts'])} artifact(s) → {artifacts_dir}")
                    if ps.prompt_archetype:
                        ps.log("agent", f"repo archetype: {ps.prompt_archetype}")
                    ps.log("agent", f"tool calls used: {result['tool_uses']}")
                    ps.log("agent", f"tokens — input: {result['input_tokens']:,}  output: {result['output_tokens']:,}  total: {result['input_tokens'] + result['output_tokens']:,}")
        return ps
    else:
        # Dict-based state (legacy)
        if result["status"] == "failed":
            return {**state, "status": "failed", "error": result["error"], "events": events}
        if not result["artifacts"]:
            return {**state, "status": "failed", "error": "agent completed but wrote no artifacts", "events": events}
        archetype = result.get("prompt_archetype", "backend-service")
        missing_artifacts = validate_artifacts(artifacts_dir, archetype=archetype)
        quality_issues = validate_artifact_quality(artifacts_dir, archetype)
        quality_warnings = validate_artifact_warnings(artifacts_dir)
        if missing_artifacts:
            return {
                **state,
                "status": "failed",
                "error": "agent completed but missed mandatory artifacts: " + ", ".join(missing_artifacts),
                "events": events,
            }
        if quality_issues:
            return {
                **state,
                "status": "failed",
                "error": "agent output failed validation: " + "; ".join(quality_issues),
                "events": events,
            }
        for warning in quality_warnings:
            events.append(f"[agent] warning: {warning}")
        events.append(f"[agent] {len(result['artifacts'])} artifact(s) → {artifacts_dir}")
        events.append(f"[agent] tool calls used: {result['tool_uses']}")
        return {
            **state,
            "artifacts_dir": artifacts_dir,
            "events": events,
            "prompt_archetype": result.get("prompt_archetype", ""),
        }


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the agent standalone (outside the full pipeline)."""
    parser = argparse.ArgumentParser(
        description="Reverse-engineering agent — analyse a KuzuDB code graph via LLM."
    )
    parser.add_argument("--db-path", required=True, help="Path to KuzuDB database")
    parser.add_argument("--repo-path", default="", help="Path to source repository root (enables get_method_source)")
    parser.add_argument("--repo-name", default="", help="Repository name (inferred if omitted)")
    parser.add_argument("--output-dir", default="./output", help="Root directory for artifacts")
    parser.add_argument("--request", default="", help="Custom user request (optional)")
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "claude", "ollama", "openai"],
                        help="LLM provider (default: auto)")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Model name (default: claude-sonnet-4-6)")
    parser.add_argument("--base-url", default="",
                        help="API base URL (for Ollama/OpenAI-compatible endpoints)")
    parser.add_argument("--prompt", default="",
                        help="Path to system prompt .md file")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    repo_name = args.repo_name or Path(args.db_path).parent.name

    try:
        KuzuBackend(args.db_path)  # connectivity check
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    provider = create_provider(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
    )

    use_anthropic = args.provider in ("claude", "anthropic") or (
        args.provider == "auto" and args.model.startswith("claude")
    )

    prompt_path = args.prompt or str(_PROMPT_PATH)
    artifacts_dir = str(Path(args.output_dir) / repo_name)
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    result = run_supervisor_agent(
        provider=provider,
        kuzu_path=args.db_path,
        repo_path=args.repo_path,
        repo_name=repo_name,
        artifacts_dir=artifacts_dir,
        base_prompt_path=prompt_path,
        max_turns=args.max_turns,
        verbose=args.verbose,
        use_anthropic_format=use_anthropic,
    )

    elapsed = time.time() - t0
    print(f"\nElapsed:    {elapsed:.1f}s")
    print(f"Status:     {result['status']}")
    print(f"Tool calls: {result['tool_uses']}")
    print(f"Tokens:     {result['input_tokens']:,} input / {result['output_tokens']:,} output / {result['input_tokens'] + result['output_tokens']:,} total")
    if result["artifacts"]:
        print("Artifacts written:")
        for a in result["artifacts"]:
            print(f"  {a}")
    if result["error"]:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
