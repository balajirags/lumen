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
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

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
        tool_defs = toolkit.anthropic_tool_definitions() + [_WRITE_ARTIFACT_ANTHROPIC]
    else:
        tool_defs = toolkit.openai_tool_definitions() + [_WRITE_ARTIFACT_OPENAI]
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
            return {
                "status": "failed",
                "error": f"LLM API error: {e}",
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
        print(f"{tag} turn {turn}/{max_turns}  {first_tool}", flush=True)

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
        return {
            "status": "done",
            "error": None,
            "artifacts": artifacts,
            "events": events,
            "tool_uses": tool_uses,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }

    log(f"{tag} tokens — input: {total_input_tokens:,}  output: {total_output_tokens:,}  total: {total_input_tokens + total_output_tokens:,}")

    return {
        "status": "done",
        "error": None,
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


def _detect_repo_type(orientation_summary: str) -> str:
    """Detect whether the repo is a frontend, backend, or fullstack codebase.

    Uses keyword matching on the orientation summary produced by Phase 1.
    Returns one of: 'frontend', 'backend', 'fullstack'.
    """
    s = orientation_summary.lower()
    frontend_signals = [
        "react", "vue", "angular", "svelte", "component", "jsx", "tsx",
        "next.js", "nuxt", "vite", "webpack", "hook", "redux", "zustand",
        "jotai", "recoil", "mobx", "tailwind", "css module",
    ]
    backend_signals = [
        "controller", "service", "repository", "entity", "spring", "django",
        "flask", "fastapi", "express", "annotation", "middleware", "endpoint",
        "rest api", "graphql server", "grpc", "database", "orm", "hibernate",
    ]
    has_frontend = any(w in s for w in frontend_signals)
    has_backend = any(w in s for w in backend_signals)
    if has_frontend and has_backend:
        return "fullstack"
    return "frontend" if has_frontend else "backend"


def _build_phase_system_prompt(
    base_prompt: str,
    phase_file: str,
    orientation_summary: str,
    db_path: str,
    repo_name: str,
    output_root: str,
) -> str:
    """Combine base prompt + phase-specific instructions + runtime context."""
    phase_path = _PHASE_PROMPTS_DIR / phase_file
    phase_instructions = phase_path.read_text(encoding="utf-8") if phase_path.exists() else ""

    prompt = base_prompt
    prompt += f"\n\n---\n## Orientation Summary\n\n{orientation_summary}\n"
    prompt += f"\n\n{phase_instructions}"
    prompt += _GRAPH_CONVENTIONS
    prompt += (
        f"\n\n---\n## Runtime context\n\n"
        f"- KuzuDB path: `{db_path}`\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Artifact output root: `{output_root}`\n"
        f"- Write all artifacts using the `write_artifact` tool.\n"
    )
    return prompt


def run_supervisor_agent(
    provider: LLMProvider,
    kuzu_path: str,
    repo_path: str,
    repo_name: str,
    artifacts_dir: str,
    base_prompt_path: str | Path,
    max_turns_per_phase: int = 20,
    verbose: bool = False,
    use_anthropic_format: bool = False,
    max_context_tokens: int = 120_000,
    max_source_reads: int = 15,
) -> dict[str, Any]:
    """Run the multi-phase parallel supervisor pattern.

    Phase 1:     direct toolkit query (no LLM tokens).
    Phase 2 + 3: parallel — api-analyst, architect.
    Phase 4–7:   parallel — migration-planner, c4-context, sequence-diagrams, er-diagram.
                 All seeded with Phase 2+3 artifacts. Phase 4 failure is fatal;
                 Phases 5–7 (diagram subagents) are non-fatal.

    Returns the same dict shape as run_loop().
    """
    # Load base prompt (evidence model, artifact contract, rules — no workflow section)
    base_path = Path(base_prompt_path)
    base_prompt = base_path.read_text(encoding="utf-8") if base_path.exists() else ""
    if base_prompt.startswith("---"):
        end = base_prompt.find("---", 3)
        if end != -1:
            base_prompt = base_prompt[end + 3:].lstrip()

    all_events: list[str] = []
    all_artifacts: list[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_tool_uses = 0

    def log(msg: str) -> None:
        all_events.append(msg)
        print(msg, flush=True)

    # ------------------------------------------------------------------
    # Phase 1: direct toolkit query — no LLM needed
    # ------------------------------------------------------------------
    log("[supervisor] Phase 1 — orientation (direct toolkit query)")
    try:
        p1_backend = KuzuBackend(kuzu_path)
        p1_toolkit = ReverseEngineerToolkit(p1_backend, repo_path=repo_path)
        orientation_summary = p1_toolkit.call("get_architecture_summary")
        log(f"[supervisor] Orientation complete ({len(orientation_summary):,} chars)")
    except Exception as e:
        return {
            "status": "failed",
            "error": f"Phase 1 orientation failed: {e}",
            "artifacts": all_artifacts,
            "events": all_events,
            "tool_uses": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    # ------------------------------------------------------------------
    # Detect repo type and select appropriate prompts
    # ------------------------------------------------------------------
    repo_type = _detect_repo_type(orientation_summary)
    log(f"[supervisor] Detected repo type: {repo_type}")

    if repo_type == "frontend":
        frontend_base_path = _PHASE_PROMPTS_DIR / "re-prompt-frontend.md"
        if frontend_base_path.exists():
            raw = frontend_base_path.read_text(encoding="utf-8")
            if raw.startswith("---"):
                raw = raw[raw.find("---", 3) + 3:].lstrip()
            base_prompt = raw
        phase2_file = "phase2-frontend-inventory.md"
        phase3_file = "phase3-frontend-architecture.md"
        phase4_file = "phase4-frontend-migration.md"
        phase2_req = (
            "Analyse the component structure, routing, and state management. "
            "Write current-state/inventory.md. Stop after writing that artifact."
        )
        phase3_req = (
            "Analyse component hierarchy, data flow patterns, and feature organisation. "
            "Write architecture/system-overview.md and domain/domain-analysis.md. "
            "Stop after writing both artifacts."
        )
    else:
        phase2_file = "phase2-inventory.md"
        phase3_file = "phase3-architecture.md"
        phase4_file = "phase4-migration.md"
        phase2_req = (
            "Analyse the API surface and module structure. "
            "Write current-state/inventory.md. Stop after writing that artifact."
        )
        phase3_req = (
            "Analyse architecture patterns and domain model. "
            "Write architecture/system-overview.md and domain/domain-analysis.md. "
            "Stop after writing both artifacts."
        )

    # ------------------------------------------------------------------
    # Helper: run one phase in its own thread
    # ------------------------------------------------------------------
    def _run_phase(
        phase_name: str,
        phase_file: str,
        user_request: str,
        base: str = base_prompt,
    ) -> tuple[str, dict]:
        # Each phase gets its own backend + toolkit (KuzuDB connections are not thread-safe)
        backend = KuzuBackend(kuzu_path)
        toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
        system_prompt = _build_phase_system_prompt(
            base, phase_file, orientation_summary, kuzu_path, repo_name, artifacts_dir
        )
        result = run_loop(
            provider=provider,
            toolkit=toolkit,
            system_prompt=system_prompt,
            user_request=user_request,
            output_root=artifacts_dir,
            max_turns=max_turns_per_phase,
            verbose=verbose,
            use_anthropic_format=use_anthropic_format,
            max_context_tokens=max_context_tokens,
            max_source_reads=max_source_reads,
            phase_label=phase_name,
        )
        return phase_name, result

    # ------------------------------------------------------------------
    # Phase 2 + Phase 3: parallel
    # ------------------------------------------------------------------
    log("[supervisor] spawning subagent/api-analyst + subagent/architect in parallel…")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(_run_phase, "subagent/api-analyst", phase2_file, phase2_req): "subagent/api-analyst",
            pool.submit(_run_phase, "subagent/architect", phase3_file, phase3_req): "subagent/architect",
        }
        for future in as_completed(futures):
            phase_name, result = future.result()
            all_events.extend(result["events"])
            all_artifacts.extend(result["artifacts"])
            total_input_tokens += result["input_tokens"]
            total_output_tokens += result["output_tokens"]
            total_tool_uses += result["tool_uses"]
            log(f"[supervisor] {phase_name} done — {len(result['artifacts'])} artifact(s)")
            if result["status"] == "failed":
                return {
                    "status": "failed",
                    "error": f"{phase_name} failed: {result['error']}",
                    "artifacts": all_artifacts,
                    "events": all_events,
                    "tool_uses": total_tool_uses,
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                }

    # ------------------------------------------------------------------
    # Phase 4 + Phase 5: parallel — both seeded with Phase 2+3 artifacts
    # ------------------------------------------------------------------
    def _read_artifact(rel_path: str) -> str:
        p = Path(artifacts_dir) / rel_path
        return p.read_text(encoding="utf-8") if p.exists() else "_Not available._"

    prior_context = (
        "\n\n---\n## Prior Phase Results (do not re-query these)\n\n"
        f"### current-state/inventory.md\n\n{_read_artifact('current-state/inventory.md')[:3_000]}\n\n"
        f"### architecture/system-overview.md\n\n{_read_artifact('architecture/system-overview.md')[:2_000]}\n\n"
        f"### domain/domain-analysis.md\n\n{_read_artifact('domain/domain-analysis.md')[:2_000]}\n"
    )

    base_with_context = base_prompt + prior_context

    log("[supervisor] spawning subagent/migration-planner + subagent/c4-context + subagent/sequence-diagrams + subagent/er-diagram in parallel…")

    phase4_req = (
        "Analyse hotspots and migration risk. Write migration/roadmap.md, "
        "target-state/blueprint.md, and manifests/artifacts.json. "
        "Write target-state/openapi/*.yaml only if the graph has sufficient endpoint detail."
    )
    phase5_req = (
        "Identify external integration points. "
        "Write architecture/c4-context.md with a Mermaid C4Context diagram. "
        "Stop after writing that artifact."
    )
    phase6_req = (
        "Trace the top 3–5 user-facing flows. "
        "Write architecture/sequence-diagrams.md with Mermaid sequenceDiagram blocks. "
        "Stop after writing that artifact."
    )
    phase7_req = (
        "Map the data model and entity relationships. "
        "Write domain/er-diagram.md with a Mermaid erDiagram and bounded context ownership table. "
        "Stop after writing that artifact."
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_run_phase, "subagent/migration-planner", phase4_file,
                        phase4_req, base_with_context): "subagent/migration-planner",
            pool.submit(_run_phase, "subagent/c4-context", "phase5-c4-context.md",
                        phase5_req, base_with_context): "subagent/c4-context",
            pool.submit(_run_phase, "subagent/sequence-diagrams", "phase6-sequence-diagrams.md",
                        phase6_req, base_with_context): "subagent/sequence-diagrams",
            pool.submit(_run_phase, "subagent/er-diagram", "phase7-er-diagram.md",
                        phase7_req, base_with_context): "subagent/er-diagram",
        }
        for future in as_completed(futures):
            phase_name, result = future.result()
            all_events.extend(result["events"])
            all_artifacts.extend(result["artifacts"])
            total_input_tokens += result["input_tokens"]
            total_output_tokens += result["output_tokens"]
            total_tool_uses += result["tool_uses"]
            log(f"[supervisor] {phase_name} done — {len(result['artifacts'])} artifact(s)")
            if result["status"] == "failed":
                if phase_name == "subagent/migration-planner":
                    return {
                        "status": "failed",
                        "error": f"Phase 4 failed: {result['error']}",
                        "artifacts": all_artifacts,
                        "events": all_events,
                        "tool_uses": total_tool_uses,
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                    }
                else:
                    # Diagram subagents (Phase 5/6/7) are non-fatal — log and continue
                    log(f"[supervisor] WARNING: {phase_name} failed: {result['error']} — continuing")

    log(
        f"[supervisor] all phases complete — "
        f"artifacts: {len(all_artifacts)}  "
        f"tokens: {total_input_tokens:,} in / {total_output_tokens:,} out  "
        f"tool uses: {total_tool_uses}"
    )

    return {
        "status": "done",
        "error": None,
        "artifacts": all_artifacts,
        "events": all_events,
        "tool_uses": total_tool_uses,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
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

    repo_name = Path(kuzu_path).parent.name if kuzu_path else "unknown"

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

    # Turns are distributed across 3 phases; divide the budget evenly
    max_turns_per_phase = max(10, max_turns // 3)

    resolved_prompt = prompt_path or str(_PROMPT_PATH)

    result = run_supervisor_agent(
        provider=provider,
        kuzu_path=kuzu_path,
        repo_path=repo_path,
        repo_name=repo_name,
        artifacts_dir=artifacts_dir,
        base_prompt_path=resolved_prompt,
        max_turns_per_phase=max_turns_per_phase,
        verbose=verbose,
        use_anthropic_format=use_anthropic,
        max_context_tokens=max_context_tokens,
    )

    events.extend(result["events"])

    if not is_dict:
        ps.events = events
        ps.input_tokens = result["input_tokens"]
        ps.output_tokens = result["output_tokens"]
        ps.tool_uses = result["tool_uses"]
        if result["status"] == "failed":
            ps.status = "failed"
            ps.error = result["error"]
        else:
            ps.artifacts_dir = artifacts_dir
            if not result["artifacts"]:
                ps.status = "failed"
                ps.error = "agent completed but wrote no artifacts"
            else:
                ps.log("agent", f"{len(result['artifacts'])} artifact(s) → {artifacts_dir}")
                ps.log("agent", f"tool calls used: {result['tool_uses']}")
                ps.log("agent", f"tokens — input: {result['input_tokens']:,}  output: {result['output_tokens']:,}  total: {result['input_tokens'] + result['output_tokens']:,}")
        return ps
    else:
        # Dict-based state (legacy)
        if result["status"] == "failed":
            return {**state, "status": "failed", "error": result["error"], "events": events}
        if not result["artifacts"]:
            return {**state, "status": "failed", "error": "agent completed but wrote no artifacts", "events": events}
        events.append(f"[agent] {len(result['artifacts'])} artifact(s) → {artifacts_dir}")
        events.append(f"[agent] tool calls used: {result['tool_uses']}")
        return {**state, "artifacts_dir": artifacts_dir, "events": events}


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
        max_turns_per_phase=max(10, args.max_turns // 3),
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
