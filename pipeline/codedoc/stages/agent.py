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

from codedoc.archetype_registry import ARCHETYPES, archetype_definition
from codedoc.artifact_planner import (
    artifact_status_snapshot,
    build_artifact_plan,
    classify_artifact_path,
    planned_artifacts_by_class,
)
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


def _find_oldest_assistant_turn(messages: list[dict]) -> tuple[int | None, int | None]:
    """Return (start, end) indices of the first assistant+tool turn after any leading messages.

    Skips system, user, and pinned-user messages at the front. Returns (None, None) if
    no trimmable turn exists or if trimming would remove the last turn (unsafe).
    """
    # Find first assistant message that has tool_calls
    trim_start = None
    for i in range(len(messages)):
        if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
            trim_start = i
            break

    if trim_start is None:
        return None, None

    # Collect all immediately following tool-result messages
    trim_end = trim_start + 1
    for j in range(trim_start + 1, len(messages)):
        if messages[j].get("role") == "tool":
            trim_end = j + 1
        else:
            break

    # Don't trim if this is the last turn (nothing left to work with)
    if trim_end >= len(messages):
        return None, None

    return trim_start, trim_end


def _trim_context(messages: list[dict], max_turns: int = 1) -> tuple[list[dict], int]:
    """Remove up to `max_turns` oldest assistant+tool turn groups.

    When over budget by a large margin, removing multiple turns at once prevents
    the 17-22 single-removal loops seen on large repos. All removals are reported
    in a single combined placeholder so the LLM sees one coherent gap notice.

    Pinned user messages near the front (inserted before the agentic loop) are
    never removed — only assistant+tool turns are eligible.

    Returns:
        (trimmed_messages, total_number_of_messages_removed)
    """
    removed_total = 0
    turn_summaries: list[str] = []

    for turn_n in range(max_turns):
        trim_start, trim_end = _find_oldest_assistant_turn(messages)
        if trim_start is None:
            break
        n = trim_end - trim_start
        removed_total += n
        turn_summaries.append(f"{n} msgs (turn starting at pos {trim_start})")
        messages = messages[:trim_start] + messages[trim_end:]

    if removed_total == 0:
        return messages, 0

    summary = "; ".join(turn_summaries)
    placeholder = {
        "role": "user",
        "content": (
            f"[Context pruned: removed {removed_total} earlier messages "
            f"({len(turn_summaries)} turn(s)) to stay within context limit. "
            f"Details: {summary}. Continue from available context.]"
        ),
    }
    # Insert placeholder just before the first remaining assistant turn
    first_assistant = next(
        (i for i, m in enumerate(messages) if m.get("role") == "assistant"),
        len(messages),
    )
    trimmed = messages[:first_assistant] + [placeholder] + messages[first_assistant:]
    return trimmed, removed_total


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
    pinned_user_messages: list[str] | None = None,
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
    # Pinned user messages are inserted right after the initial request and are
    # never removed by _trim_context() because that function only removes
    # assistant+tool turns. This keeps critical evidence (e.g. pre-fetched
    # endpoint data) visible throughout the entire session.
    if pinned_user_messages:
        for pinned in pinned_user_messages:
            messages.append({"role": "user", "content": pinned})
    events: list[str] = []
    artifacts: list[str] = []
    tool_uses = 0
    tool_call_counts: dict[str, int] = {}
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
                "tool_call_counts": tool_call_counts,
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
            tool_call_counts[tc.name] = tool_call_counts.get(tc.name, 0) + 1
            args_summary = json.dumps(tc.arguments)
            if len(args_summary) > 120:
                args_summary = args_summary[:120] + "…"
            log(f"{tool_tag} {tc.name}({args_summary})")

            result_text = _dispatch_tool(tc, toolkit, output_root, artifacts, extra_tools, source_reads_remaining)

            log(f"{tool_tag} → {result_text[:150]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        # Prune oldest turn group(s) if over the context token budget.
        # Remove 3 turns at once when heavily over-budget to avoid the
        # 17-22 single-removal loops observed on large repos.
        if max_context_tokens > 0 and total_input_tokens > max_context_tokens:
            overage = total_input_tokens - max_context_tokens
            turns_to_remove = 3 if overage > 20_000 else 1
            messages, removed = _trim_context(messages, max_turns=turns_to_remove)
            if removed:
                log(f"{tag} context pruned: removed {removed} messages "
                    f"(over budget by {overage:,} tokens, {turns_to_remove} turn(s) removed)")
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
            "tool_call_counts": tool_call_counts,
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
        "tool_call_counts": tool_call_counts,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
    }


# ---------------------------------------------------------------------------
# Supervisor: parallel Phase 2 + Phase 3, then Phase 4
# ---------------------------------------------------------------------------

_PHASE_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_EXECUTIVE_SUMMARY_PROMPT = _PHASE_PROMPTS_DIR / "executive-summary.md"


_ANALYST_PROMPT_FILES: dict[str, str] = {
    "analyst/domain": "analyst-domain.md",
    "analyst/flows": "analyst-flows.md",
    "analyst/tech": "analyst-tech.md",
}

_ARCHETYPE_GUIDANCE_FILES: dict[str, str] = {
    key: definition.guidance_file for key, definition in ARCHETYPES.items()
}

_ARTIFACT_CONTRACTS: dict[str, dict[str, list[str]]] = {
    key: {
        "analyst_required": list(definition.analyst_required),
        "analyst_optional": list(definition.analyst_optional),
        "architect_required": list(definition.architect_required),
        "architect_sequence": list(definition.architect_sequence),
        "forbidden": list(definition.forbidden),
    }
    for key, definition in ARCHETYPES.items()
}


def _artifact_contract(archetype: str) -> dict[str, list[str]]:
    definition = archetype_definition(archetype)
    return {
        "analyst_required": list(definition.analyst_required),
        "analyst_optional": list(definition.analyst_optional),
        "architect_required": list(definition.architect_required),
        "architect_sequence": list(definition.architect_sequence),
        "forbidden": list(definition.forbidden),
    }


def _required_artifacts(archetype: str) -> list[str]:
    return planned_artifacts_by_class(build_artifact_plan(archetype), "core", required_only=True) + planned_artifacts_by_class(build_artifact_plan(archetype), "target", required_only=True) + ["manifests/artifacts.json"]


def _current_state_artifacts(archetype: str) -> list[str]:
    plan = build_artifact_plan(archetype)
    return [
        item["path"]
        for item in plan["artifacts"]
        if item["class"] in {"core", "conditional"} and not str(item["path"]).startswith("summary/") and not str(item["path"]).startswith("target-state/")
    ]


def _architect_sequence(archetype: str) -> list[str]:
    return _artifact_contract(archetype)["architect_sequence"]


def _write_machine_manifest(
    artifacts_dir: str,
    repo_name: str,
    archetype: str,
    artifact_plan: dict[str, Any] | None = None,
    artifact_omissions: list[dict[str, str]] | None = None,
    repo_metrics: dict[str, Any] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> str:
    root = Path(artifacts_dir)
    manifest_path = root / "manifests" / "artifacts.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan = artifact_plan or build_artifact_plan(archetype)
    entries, default_omitted = artifact_status_snapshot(artifacts_dir, plan)
    omitted_by_file: dict[str, dict[str, str]] = {}
    for omission in list(artifact_omissions or []) + default_omitted:
        omitted_by_file[omission["file"]] = omission
    omitted = list(omitted_by_file.values())

    manifest = {
        "version": "1.0",
        "repo_name": repo_name,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "artifacts": entries,
        "omitted": omitted,
        "archetype": archetype,
        "primary_repo_type": plan.get("primary_repo_type", archetype),
        "capabilities": plan.get("capabilities", []),
        "size_profile": plan.get("size_profile", "small"),
        "repo_metrics": {
            "loc": int((repo_metrics or {}).get("total_loc", 0) or 0),
            "source_files": int((repo_metrics or {}).get("total_source_files", 0) or 0),
            "size_band": (repo_metrics or {}).get("size_band", ""),
            "risk_level": (repo_metrics or {}).get("risk_level", ""),
        },
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
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


def _build_analyst_requests(archetype: str) -> dict[str, str]:
    return dict(archetype_definition(archetype).analyst_requests)


def validate_artifacts(
    artifacts_dir: str,
    required_artifacts: list[str] | None = None,
    archetype: str = "backend-service",
    artifact_plan: dict[str, Any] | None = None,
) -> list[str]:
    if required_artifacts is not None:
        required = required_artifacts
    else:
        plan = artifact_plan or build_artifact_plan(archetype)
        required = [item["path"] for item in plan.get("artifacts", []) if item.get("required")]
    return [rel_path for rel_path in required if not (Path(artifacts_dir) / rel_path).exists()]


def _conditional_artifact_omissions(artifacts_dir: str, artifact_plan: dict[str, Any]) -> list[dict[str, str]]:
    root = Path(artifacts_dir)
    omissions: list[dict[str, str]] = []
    for item in artifact_plan.get("artifacts", []):
        if item.get("class") != "conditional":
            continue
        rel_path = str(item["path"])
        if (root / rel_path).exists():
            continue
        omissions.append(
            {
                "file": rel_path,
                "reason": "Conditional artifact omitted because evidence was weak, the repo shape did not support it, or recovery deprioritized it.",
            }
        )
    return omissions


def _write_executive_summary(
    artifacts_dir: str,
    repo_name: str,
    primary_repo_type: str,
    capabilities: list[str],
    language_categories: list[str],
    artifact_plan: dict[str, Any],
    artifact_omissions: list[dict[str, str]],
) -> str:
    """Deterministic fallback summary writer used if the dedicated summary phase fails."""
    root = Path(artifacts_dir)
    summary_path = root / "summary" / "executive-summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    target_state_files = [item["path"] for item in artifact_plan.get("artifacts", []) if item["class"] == "target"]

    def _snippet(rel_path: str) -> str:
        full_path = root / rel_path
        if not full_path.exists():
            return ""
        try:
            content = full_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        for line in content.splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#") and not clean.startswith("```"):
                return clean
        return ""

    repo_descriptions = {
        "backend-service": "a backend operational service",
        "frontend-app": "a user-facing application",
        "fullstack-app": "an operational application with both user-facing and backend responsibilities",
        "library": "a reusable platform or library component",
    }
    capability_labels = {
        "http-api": "REST and service integration endpoints",
        "persistence": "transactional data management",
        "ui-routes": "user-facing application flows",
        "backend-runtime": "backend processing",
        "js-runtime": "web application delivery",
        "public-api": "consumer-facing API contracts",
    }

    business_capability = _snippet("domain/business-capabilities.md")
    context_summary = _snippet("architecture/c4-context.md")
    route_summary = _snippet("architecture/route-map.md")
    ui_api_summary = _snippet("current-state/ui-to-api-interactions.md")
    module_summary = _snippet("current-state/module-dependency-map.md")
    hotspot_snippet = _snippet("tech/coupling-hotspots.md")

    executive_opening = (
        f"`{repo_name}` appears to be {repo_descriptions.get(primary_repo_type, 'an application service')}."
    )
    if business_capability:
        executive_opening += f" Its primary business scope is: {business_capability}"
    elif context_summary:
        executive_opening += f" Current evidence indicates: {context_summary}"
    elif route_summary:
        executive_opening += f" Current evidence indicates: {route_summary}"

    if capabilities:
        mapped = [capability_labels.get(cap, cap.replace("-", " ")) for cap in capabilities[:3]]
        executive_opening += " The system currently supports " + ", ".join(mapped) + "."

    current_state_points: list[str] = []
    if context_summary:
        current_state_points.append(f"- Architecture posture: {context_summary}")
    if business_capability:
        current_state_points.append(f"- Operational scope: {business_capability}")
    if ui_api_summary:
        current_state_points.append(f"- Channel and integration model: {ui_api_summary}")
    elif route_summary:
        current_state_points.append(f"- User-facing scope: {route_summary}")
    if module_summary:
        current_state_points.append(f"- Structural posture: {module_summary}")
    if not current_state_points:
        current_state_points.append("- The codebase evidence supports a partial architecture read, but the operating model is not fully explicit.")

    pain_points: list[str] = []
    if hotspot_snippet:
        pain_points.append(f"- Coupling risk: {hotspot_snippet}")
    if artifact_omissions:
        omission_labels = ", ".join(omission["file"] for omission in artifact_omissions[:3])
        pain_points.append(f"- Decision risk: several supporting views remain partial, especially around {omission_labels}.")
    module_snippet = _snippet("current-state/module-dependency-map.md")
    if module_snippet:
        pain_points.append(f"- Boundary clarity: {module_snippet}")
    if not pain_points:
        pain_points.append("- No severe structural risks were extracted beyond the standard hotspot review.")

    recommendations: list[str] = []
    for rel_path in target_state_files:
        full_path = root / rel_path
        if not full_path.exists():
            continue
        label = Path(rel_path).stem.replace("-", " ")
        recommendations.append(f"- Use [{label}]({Path('..') / rel_path}) as the immediate delivery plan for the next modernization step.")
        if len(recommendations) >= 3:
            break
    if not recommendations:
        recommendations.append("- Complete the target-state package before committing to a delivery roadmap.")

    confidence_lines: list[str] = []
    if artifact_omissions:
        confidence_lines.append(
            "- Confidence is moderate. Core architectural and domain signals are present, but some supporting views were omitted because evidence was weak or incomplete."
        )
    else:
        confidence_lines.append("- Confidence is moderate to strong based on the generated current-state and target-state evidence.")
    if language_categories:
        confidence_lines.append(
            "- The summary is based on the indexed runtime surfaces observed across " + ", ".join(language_categories) + "."
        )

    lines = [
        "# Executive Summary",
        "",
        "## Executive Overview",
        "",
        executive_opening,
        "",
        "## Current State",
        "",
        *current_state_points,
        "",
        "## Key Risks",
        "",
        *pain_points,
        "",
        "## Recommendations",
        "",
        *recommendations,
        "",
        "## Confidence And Limitations",
        "",
    ]
    lines.extend(confidence_lines)

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(summary_path)


def _artifact_excerpt(root: Path, rel_path: str, limit: int = 2_400) -> str:
    full_path = root / rel_path
    if not full_path.exists():
        return ""
    try:
        content = full_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(content) <= limit:
        return content
    return content[:limit].rstrip() + "\n...[truncated]"


def _build_executive_summary_evidence(
    artifacts_dir: str,
    repo_name: str,
    primary_repo_type: str,
    capabilities: list[str],
    language_categories: list[str],
    artifact_plan: dict[str, Any],
    artifact_omissions: list[dict[str, str]],
    repo_metrics: dict[str, Any] | None,
) -> str:
    root = Path(artifacts_dir)
    artifact_items = artifact_plan.get("artifacts", [])
    target_state_files = [str(item["path"]) for item in artifact_items if item["class"] == "target"]
    evidence_paths = [
        "domain/business-capabilities.md",
        "architecture/c4-context.md",
        "architecture/business-journeys.md",
        "architecture/route-map.md",
        "current-state/ui-to-api-interactions.md",
        "current-state/module-dependency-map.md",
        "current-state/api-spec.yaml",
        "domain/er-diagram.md",
        "tech/coupling-hotspots.md",
        *target_state_files,
    ]
    seen: set[str] = set()
    evidence_sections: list[str] = []
    for rel_path in evidence_paths:
        if rel_path in seen:
            continue
        seen.add(rel_path)
        # Give more room to the two most information-dense artifacts.
        # coupling-hotspots has specific method names + numeric scores;
        # business-capabilities maps capabilities to concrete services.
        # The extra ~2k chars per artifact adds ~500 tokens but meaningfully
        # improves the specificity of the executive summary recommendations.
        _HIGH_VALUE_EVIDENCE = {"tech/coupling-hotspots.md", "domain/business-capabilities.md"}
        excerpt_limit = 3_500 if rel_path in _HIGH_VALUE_EVIDENCE else 2_400
        excerpt = _artifact_excerpt(root, rel_path, limit=excerpt_limit)
        if not excerpt:
            continue
        evidence_sections.append(f"### {rel_path}\n{excerpt}")

    omission_lines = (
        "\n".join(f"- {item['file']}: {item['reason']}" for item in artifact_omissions)
        if artifact_omissions
        else "- None"
    )
    metrics = repo_metrics or {}
    lines = [
        "# Executive Summary Evidence Pack",
        "",
        "## Repo Profile",
        f"- Repo name: {repo_name}",
        f"- Primary repo type: {primary_repo_type}",
        f"- Language categories: {', '.join(language_categories) if language_categories else 'unknown'}",
        f"- Capabilities: {', '.join(capabilities) if capabilities else 'not confidently classified'}",
        "",
        "## Repo Metrics",
        f"- LOC: {int(metrics.get('total_loc', 0) or 0):,}",
        f"- Source files: {int(metrics.get('total_source_files', 0) or 0):,}",
        f"- Size band: {metrics.get('size_band', 'unknown')}",
        f"- Risk level: {metrics.get('risk_level', 'unknown')}",
        "",
        "## Planned Target-State Artifacts",
        *([f"- {path}" for path in target_state_files] or ["- None"]),
        "",
        "## Omitted Or Weak-Evidence Supporting Views",
        omission_lines,
        "",
        "## Key Artifact Evidence",
        "",
    ]
    lines.extend(evidence_sections or ["No artifact evidence was available."])
    return "\n".join(lines)


def _run_executive_summary_phase(
    provider: LLMProvider,
    artifacts_dir: str,
    repo_name: str,
    primary_repo_type: str,
    capabilities: list[str],
    language_categories: list[str],
    artifact_plan: dict[str, Any],
    artifact_omissions: list[dict[str, str]],
    repo_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    system_prompt = _load_prompt_file(
        _EXECUTIVE_SUMMARY_PROMPT,
        "Write a leadership-facing executive summary in Markdown.",
    )
    evidence_pack = _build_executive_summary_evidence(
        artifacts_dir,
        repo_name,
        primary_repo_type,
        capabilities,
        language_categories,
        artifact_plan,
        artifact_omissions,
        repo_metrics,
    )
    user_request = (
        "Produce `summary/executive-summary.md` from the evidence pack below.\n\n"
        "Requirements:\n"
        "- leadership-facing\n"
        "- strong synthesis and prioritization\n"
        "- recommendations must be concrete and evidence-based\n"
        "- no artifact index\n"
        "- return Markdown only\n\n"
        f"{evidence_pack}"
    )

    try:
        response = chat_with_retry(
            provider,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_request},
            ],
            tools=None,
            tool_choice="auto",
        )
        content = (response.content or "").strip()
        if not content:
            raise RuntimeError("empty executive summary response")

        summary_path = Path(artifacts_dir) / "summary" / "executive-summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        return {
            "status": "done",
            "path": str(summary_path),
            "events": ["[supervisor] executive summary generated via dedicated leadership brief prompt"],
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
    except Exception as exc:
        summary_path = _write_executive_summary(
            artifacts_dir,
            repo_name,
            primary_repo_type,
            capabilities,
            language_categories,
            artifact_plan,
            artifact_omissions,
        )
        return {
            "status": "fallback",
            "path": summary_path,
            "events": [f"[supervisor] executive summary fallback used — {type(exc).__name__}: {exc}"],
            "input_tokens": 0,
            "output_tokens": 0,
        }


def _recover_missing_current_state_artifacts(
    provider: LLMProvider,
    kuzu_path: str,
    repo_path: str,
    repo_name: str,
    artifacts_dir: str,
    primary_repo_type: str,
    orientation_summary: str,
    repo_metrics: dict[str, Any] | None,
    use_anthropic_format: bool,
    max_context_tokens: int,
    verbose: bool,
    missing_paths: list[str],
    analyst_name: str = "analyst/flows",
) -> dict[str, Any]:
    if not missing_paths:
        return {"status": "done", "artifacts": [], "events": [], "tool_uses": 0, "tool_call_counts": {}, "input_tokens": 0, "output_tokens": 0}
    backend = KuzuBackend(kuzu_path)
    toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
    system_prompt = _build_analyst_system_prompt(
        analyst_name, kuzu_path, repo_name, orientation_summary, primary_repo_type, repo_metrics=repo_metrics
    )
    user_request = (
        "Write ONLY these missing current-state artifacts now, in order:\n"
        + "\n".join(f"- {path}" for path in missing_paths)
        + "\nUse graph tools as needed, but stop as soon as the listed artifacts are written. "
        "Do not write target-state artifacts."
    )
    max_turns = max(8, len(missing_paths) * 2)
    if "current-state/api-spec.yaml" in missing_paths:
        user_request += (
            "\nFor `current-state/api-spec.yaml`, produce an OpenAPI 3.0 YAML grounded in observed HTTP endpoints. "
            "If request/response schemas are incomplete, keep them minimal and mark uncertain fields conservatively in descriptions rather than omitting the endpoint."
        )
        max_turns = max(max_turns, 24)
    return run_loop(
        provider=provider,
        toolkit=toolkit,
        system_prompt=system_prompt,
        user_request=user_request,
        output_root=artifacts_dir,
        max_turns=max_turns,
        verbose=verbose,
        use_anthropic_format=use_anthropic_format,
        max_context_tokens=max_context_tokens,
        max_source_reads=0,
        include_write_artifact=True,
        extra_tools={"write_c4_artifact": lambda **kwargs: write_c4_artifact(artifacts_dir, **kwargs)},
        extra_tool_defs=[WRITE_C4_ARTIFACT_ANTHROPIC if use_anthropic_format else WRITE_C4_ARTIFACT_OPENAI],
        phase_label="analyst/recovery",
    )


def _controlled_frontend_source_confirmation(
    toolkit: ReverseEngineerToolkit,
    ui_api_map: str,
    *,
    max_reads: int = 3,
) -> str:
    """Confirm unresolved UI/API mappings with a tiny targeted source-read budget."""
    if max_reads <= 0:
        return ""
    unresolved = "no direct endpoint match" in ui_api_map.lower()
    if not unresolved:
        return ""

    candidates: list[str] = []
    for line in ui_api_map.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in ("ui", "client"):
            value = str(row.get(key, "") or "").strip()
            if value and value not in candidates:
                candidates.append(value)

    sections: list[str] = []
    for candidate in candidates[:max_reads]:
        snippet = toolkit.call("get_method_source", method_name=candidate)
        lowered = snippet.lower()
        if (
            not snippet
            or "not found in graph" in lowered
            or "no file path" in lowered
            or "cannot extract" in lowered
            or "file not found" in lowered
            or "read error" in lowered
        ):
            continue
        sections.append(f"### {candidate}\n```text\n{snippet}\n```")

    return "\n\n".join(sections)


def _backfill_required_artifacts(kuzu_path: str, repo_path: str, artifacts_dir: str, archetype: str) -> list[str]:
    generated: list[str] = []
    root = Path(artifacts_dir)
    backend = KuzuBackend(kuzu_path)
    toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)

    route_target = root / "architecture" / "route-map.md"
    if archetype in {"frontend-app", "fullstack-app"} and not route_target.exists():
        route_summary = toolkit.call("get_route_component_map")
        if "UI Entry Surfaces (SPA Fallback)" in route_summary:
            intro = (
                "# Route Map\n\n"
                "## UI Entry Surface [Observed]\n\n"
                "This frontend does not expose a strong router/page graph. The current-state view is therefore centered on the SPA entry modules and top-level rendered components captured in the graph.\n\n"
                "## Entry Modules And Root Components [Observed]\n\n"
                "```text\n"
                f"{route_summary}\n"
                "```\n\n"
                "## Interpretation [Inferred]\n\n"
                "- Treat the listed App/main modules as UI entry surfaces rather than URL routes.\n"
                "- Treat the rendered child components as the primary feature shells or tabs exposed by the SPA.\n"
                "- If a separate routing layer exists, it is not strongly represented in the current graph and should be documented as a follow-up gap.\n"
            )
            content = intro
        else:
            content = (
                "# Route Map\n\n"
                "## Route And Page Surface [Observed]\n\n"
                "The following route/page/layout view was generated directly from the knowledge graph.\n\n"
                "```text\n"
                f"{route_summary}\n"
                "```\n"
            )
        _write_artifact(artifacts_dir, "architecture/route-map.md", content)
        generated.append(str(route_target))

    component_target = root / "architecture" / "component-boundaries.md"
    if archetype in {"frontend-app", "fullstack-app"} and not component_target.exists():
        component_tree = toolkit.call("get_component_tree")
        boundaries = toolkit.call("get_component_boundary_map")
        content = (
            "# Component Boundaries\n\n"
            "## Component Tree [Observed]\n\n"
            "```text\n"
            f"{component_tree}\n"
            "```\n\n"
            "## Boundary Summary [Observed]\n\n"
            "```text\n"
            f"{boundaries}\n"
            "```\n"
        )
        _write_artifact(artifacts_dir, "architecture/component-boundaries.md", content)
        generated.append(str(component_target))

    state_target = root / "current-state" / "state-management.md"
    if archetype in {"frontend-app", "fullstack-app"} and not state_target.exists():
        ownership = toolkit.call("get_state_ownership_map")
        hooks = toolkit.call("get_hook_usage_graph")
        summary = toolkit.call("get_state_management_summary")
        content = (
            "# State Management\n\n"
            "## State Ownership [Observed]\n\n"
            "```text\n"
            f"{ownership}\n"
            "```\n\n"
            "## Hook And Consumer Graph [Observed]\n\n"
            "```text\n"
            f"{hooks}\n"
            "```\n\n"
            "## Store And Context Summary [Observed]\n\n"
            "```text\n"
            f"{summary}\n"
            "```\n"
        )
        _write_artifact(artifacts_dir, "current-state/state-management.md", content)
        generated.append(str(state_target))

    module_target = root / "current-state" / "module-dependency-map.md"
    if archetype in {"frontend-app", "fullstack-app"} and not module_target.exists():
        summary = toolkit.call("get_module_dependency_map")
        frontend_summary = toolkit.call("get_frontend_architecture_summary")
        content = (
            "# Module Dependency Map\n\n"
            "## Observed Dependency Summary [Observed]\n\n"
            "The following summary was generated directly from the knowledge graph.\n\n"
            "```text\n"
            f"{summary}\n"
            "```\n\n"
            "## Frontend Architecture Summary [Observed]\n\n"
            "```text\n"
            f"{frontend_summary}\n"
            "```\n"
        )
        _write_artifact(artifacts_dir, "current-state/module-dependency-map.md", content)
        generated.append(str(module_target))

    ui_api_target = root / "current-state" / "ui-to-api-interactions.md"
    if archetype in {"frontend-app", "fullstack-app"} and not ui_api_target.exists():
        route_summary = toolkit.call("get_route_component_map")
        client_summary = toolkit.call("get_api_client_summary")
        endpoint_summary = toolkit.call("get_ui_to_api_call_map")
        source_confirmation = _controlled_frontend_source_confirmation(toolkit, endpoint_summary)
        if "UI Module Imports To API Modules (Fallback)" in endpoint_summary:
            content = (
                "# UI to API Interactions\n\n"
                "## UI Entry Surface [Observed]\n\n"
                "The current graph does not prove direct component-to-endpoint call chains, but it does show the frontend entry modules and their imported API modules.\n\n"
                "```text\n"
                f"{route_summary}\n"
                "```\n\n"
                "## API Modules And Fetch Layers [Observed]\n\n"
                "```text\n"
                f"{client_summary}\n"
                "```\n\n"
                "## UI Module To API Module Links [Observed]\n\n"
                "```text\n"
                f"{endpoint_summary}\n"
                "```\n\n"
                "## Interpretation [Inferred]\n\n"
                "- Treat the imported API modules as the current frontend-to-backend integration seam.\n"
                "- Treat endpoint matches in this view as probable unless the graph shows a direct call path.\n"
                "- Use this artifact to identify which UI entry surfaces depend on which API wrapper modules before refining endpoint-level mappings.\n"
            )
        else:
            content = (
                "# UI to API Interactions\n\n"
                "## Route And Component Surface [Observed]\n\n"
                "The following route and UI entry-point summary was generated directly from the knowledge graph.\n\n"
                "```text\n"
                f"{route_summary}\n"
                "```\n\n"
                "## API Clients And Fetch Layers [Observed]\n\n"
                "The following client-side API usage summary was generated directly from the knowledge graph.\n\n"
                "```text\n"
                f"{client_summary}\n"
                "```\n\n"
                "## UI To Client To Endpoint Map [Observed]\n\n"
                "The following UI/client/endpoint summary was generated directly from the knowledge graph.\n\n"
                "```text\n"
                f"{endpoint_summary}\n"
                "```\n\n"
                "## Interaction Notes [Observed]\n\n"
                "- Use the route/component section as the UI entry view.\n"
                "- Use the API client section to identify fetch wrappers, query hooks, or service modules.\n"
                "- Use the call map section to trace observed UI-to-client edges and probable endpoint matches.\n"
            )
        if source_confirmation:
            content += (
                "\n## Targeted Source Confirmation [Direct Code]\n\n"
                "The graph alone did not fully resolve all UI-to-endpoint mappings, so the following targeted source snippets were read to confirm the most relevant call paths.\n\n"
                f"{source_confirmation}\n"
            )
        _write_artifact(artifacts_dir, "current-state/ui-to-api-interactions.md", content)
        generated.append(str(ui_api_target))

    return generated


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
    artifact_plan = build_artifact_plan(archetype, repo_metrics)
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
    required_artifacts = "\n".join(
        f"  - `{path}`"
        for path in [item["path"] for item in artifact_plan["artifacts"] if item["class"] in {"core", "conditional"} and item["required"]]
    )
    prompt += (
        f"\n\n---\n## Runtime context\n\n"
        f"- KuzuDB path: `{db_path}`\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Repo archetype: `{archetype}`\n"
        f"- Artifact output root: see write_artifact tool\n"
        f"- Use write_artifact to write each artifact to disk.\n"
        f"- Required current-state artifacts for this repo type:\n{required_artifacts}\n"
        f"- Conditional artifacts may be omitted if evidence is weak; do not fabricate them.\n"
        f"- If you emit PlantUML, fence every diagram as ` ```plantuml ` with `@startuml` / `@enduml`.\n"
        f"- Treat current-state artifacts as evidence documents: do not invent services, retries, auth flows, initialization steps, polling systems, or external integrations unless they are directly supported by graph evidence.\n"
        f"- Prefer `[Observed]`, `[Inferred]`, and `[Unknown]` correctly; weak evidence must not be written as `[Observed]`.\n"
        f"- Do NOT call get_method_source.\n"
    )
    if analyst_name == "analyst/flows":
        prompt += (
            "\n"
            "- **Start with `get_workflows`** — it returns pre-computed end-to-end traces (HTTP method + path + ordered steps). Each Workflow maps directly to one section in business-journeys.md.\n"
            "- **Use `get_workflow_steps(workflow_name)`** to get the ordered method chain for each Workflow. This gives you the exact sequence diagram participants and arrows without manual Cypher.\n"
            "- Only fall back to `get_api_endpoints`, `get_route_component_map`, `get_ui_to_api_call_map`, `get_api_client_summary`, `get_entry_points` if `get_workflows` returns no results.\n"
            "- If `get_route_map` or `get_route_component_map` reports no route-like frontend structures, pivot immediately: write from API/client/integration evidence rather than trying to rediscover UI routes.\n"
            "- Limit ad hoc `query` / `execute_cypher` use to at most one targeted fallback after the standard tools fail to answer a specific required artifact question.\n"
            "- Do not spend multiple turns debugging Cypher. If a query fails once, fall back to the existing toolkit evidence and write the artifact with explicit gaps.\n"
        )
    if analyst_name == "analyst/domain":
        prompt += (
            "\n"
            "- **Start with `get_domains`** — it returns pre-computed Domain clusters with cohesion scores and heuristic labels (Service, Controller, Repository, Model). Use one Domain per capability section.\n"
            "- Only fall back to `get_architecture_overview` and `get_package_contents` if `get_domains` returns no results.\n"
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

    # Inject current-state artifacts written by analysts.
    # Use an adaptive per-artifact char limit so the total passed to the architect
    # stays within ~80k chars regardless of how many artifacts were produced.
    # This prevents truncation at the model's hard context limit (e.g. 32k on local models).
    #   4 artifacts (small backend):   80k / 4  = 20k each  (unchanged)
    #   8 artifacts (fullstack):        80k / 8  = 10k each
    #  12 artifacts (large fullstack):  80k / 12 =  6.6k each
    MAX_ARCHITECT_TOTAL_CHARS = 80_000
    artifact_plan = build_artifact_plan(archetype, repo_metrics)
    contract = _artifact_contract(archetype)
    current_state_files = _current_state_artifacts(archetype)
    ARTIFACT_CHAR_LIMIT = min(20_000, MAX_ARCHITECT_TOTAL_CHARS // max(len(current_state_files), 1))
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
        f"- Size-aware profile: `{artifact_plan.get('size_profile', 'small')}`\n"
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
    return archetype_definition(archetype).architect_request


def _missing_target_state_artifacts(artifacts_dir: str, archetype: str) -> list[str]:
    contract = _artifact_contract(archetype)
    missing: list[str] = []
    for rel_path in contract["architect_sequence"]:
        if not rel_path.startswith("target-state/"):
            continue
        if not (Path(artifacts_dir) / rel_path).exists():
            missing.append(rel_path)
    return missing


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
    repo_archetype: str = "",
    artifact_plan: dict[str, Any] | None = None,
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
    phase_tokens: dict[str, dict[str, int]] = {}
    selected_repo_archetype = repo_archetype or str((repo_metrics or {}).get("selected_archetype", "")) or "backend-service"
    plan = artifact_plan or build_artifact_plan(selected_repo_archetype, repo_metrics)

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
        log(f"[supervisor] Orientation complete ({len(orientation_summary):,} chars)")
        log(f"[supervisor] Repo archetype selected: {selected_repo_archetype}")
    except Exception as e:
        return {
            "status": "failed",
            "error": f"Phase 1 orientation failed: {e}",
            "notes": "",
            "artifacts": all_artifacts,
            "events": all_events,
            "tool_uses": 0,
            "tool_call_counts": {},
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_archetype": selected_repo_archetype,
            "artifact_plan": plan,
            "artifact_omissions": [],
            "phase_tokens": phase_tokens,
        }

    # ------------------------------------------------------------------
    # Phase 2: 3 parallel Analysts — graph tools + write_artifact
    # ------------------------------------------------------------------
    analyst_turns = max(10, max_turns // 5)    # 24 turns for max_turns=120
    architect_turns = max(10, max_turns // 4)  # 30 turns for max_turns=120

    analyst_requests = _build_analyst_requests(selected_repo_archetype)

    def _run_analyst(name: str, user_request: str) -> tuple[str, dict]:
        backend = KuzuBackend(kuzu_path)
        toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
        analyst_system = _build_analyst_system_prompt(
            name, kuzu_path, repo_name, orientation_summary, selected_repo_archetype, repo_metrics=repo_metrics
        )

        # For the flows analyst, pre-fetch endpoint + workflow evidence BEFORE the
        # agentic loop starts and pin it as a non-prunable user message.
        # Without this, the evidence is collected in early turns then pruned away
        # before the write step, producing an empty api-spec.yaml paths section.
        pinned: list[str] | None = None
        if name == "analyst/flows":
            try:
                endpoint_data = toolkit.call("get_api_endpoints")
                workflow_data = toolkit.call("get_workflows")
                has_endpoints = (
                    endpoint_data
                    and "No entry points" not in endpoint_data
                    and "No API endpoints" not in endpoint_data
                )
                if has_endpoints:
                    pinned = [
                        "[Pinned evidence — survives context pruning, do not re-query these]\n\n"
                        f"## API Endpoints\n{endpoint_data}\n\n"
                        f"## Pre-computed Workflows\n{workflow_data}"
                    ]
            except Exception:
                pass  # pre-fetch is best-effort; agent will fall back to tool calls

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
            pinned_user_messages=pinned,
        )
        return name, result

    log(f"[supervisor] Phase 2 — spawning {len(analyst_requests)} analysts in parallel (domain · flows · tech)…")

    from codedoc import log as _log
    _log.start_agent_boxes()
    analyst_tool_counts: dict[str, dict[str, int]] = {}
    try:
        _log.update_workflow_phase("synthesis", status="running", tool="research fan-out")
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
                analyst_tool_counts[name] = result.get("tool_call_counts", {})
                n_artifacts = len(result["artifacts"])
                if result["status"] == "failed":
                    _log.update_agent_box(name, status="failed", tool="error")
                    log(f"[supervisor] WARNING: {name} failed: {result['error']}")
                else:
                    all_artifacts.extend(result["artifacts"])
                    _log.update_agent_box(name, status="done", tool="complete", artifacts=n_artifacts)
                    log(f"[supervisor] {name} done — {n_artifacts} artifact(s)")
                _log.print_researcher_done(name, n_artifacts)
    except Exception:
        _log.update_workflow_phase("synthesis", status="failed", tool="research fan-out")
        raise

    # Print per-researcher tool usage table so it's visible which tools each analyst called
    _log.print_tool_usage_table(analyst_tool_counts)

    backfilled = _backfill_required_artifacts(kuzu_path, repo_path, artifacts_dir, selected_repo_archetype)
    if backfilled:
        all_artifacts.extend(backfilled)
        for path in backfilled:
            all_events.append(f"[supervisor] backfilled artifact — {path}")

    missing_current_state_artifacts = validate_artifacts(
        artifacts_dir,
        required_artifacts=[
            item["path"]
            for item in plan["artifacts"]
            if item["class"] in {"core"} and item["required"] and not str(item["path"]).startswith("summary/") and not str(item["path"]).startswith("target-state/") and str(item["path"]) != "manifests/artifacts.json"
        ],
        artifact_plan=plan,
    )
    if missing_current_state_artifacts:
        _log.update_workflow_phase("synthesis", status="running", tool="current-state recovery")
        log(
            "[supervisor] recovery — missing current-state artifacts: "
            + ", ".join(missing_current_state_artifacts)
        )
        recovery_batches = [
            ("analyst/domain", [path for path in missing_current_state_artifacts if str(path).startswith("domain/")]),
            ("analyst/flows", [path for path in missing_current_state_artifacts if not str(path).startswith("domain/")]),
        ]
        for analyst_name, missing_paths in recovery_batches:
            if not missing_paths:
                continue
            recovery_result = _recover_missing_current_state_artifacts(
                provider=provider,
                kuzu_path=kuzu_path,
                repo_path=repo_path,
                repo_name=repo_name,
                artifacts_dir=artifacts_dir,
                primary_repo_type=selected_repo_archetype,
                orientation_summary=orientation_summary,
                repo_metrics=repo_metrics,
                use_anthropic_format=use_anthropic_format,
                max_context_tokens=max_context_tokens,
                verbose=verbose,
                missing_paths=missing_paths,
                analyst_name=analyst_name,
            )
            all_events.extend(recovery_result["events"])
            all_artifacts.extend(recovery_result["artifacts"])
            total_input_tokens += recovery_result["input_tokens"]
            total_output_tokens += recovery_result["output_tokens"]
            total_tool_uses += recovery_result["tool_uses"]
    _log.update_workflow_phase("synthesis", status="done", tool="evidence consolidated")

    # ------------------------------------------------------------------
    # Phase 3: Architect — reads Phase 2 artifacts, writes target-state
    # ------------------------------------------------------------------
    log("[supervisor] Phase 3 — running architect…")
    _log.update_workflow_phase("architect", status="running", tool="target-state planning")
    arch_system = _build_architect_prompt(
        orientation_summary, artifacts_dir, repo_name, selected_repo_archetype, repo_metrics=repo_metrics
    )
    arch_backend = KuzuBackend(kuzu_path)
    arch_toolkit = ReverseEngineerToolkit(arch_backend, repo_path=repo_path)
    arch_result = run_loop(
        provider=provider,
        toolkit=arch_toolkit,
        system_prompt=arch_system,
        user_request=_build_architect_request(selected_repo_archetype),
        output_root=artifacts_dir,
        max_turns=architect_turns,
        verbose=verbose,
        use_anthropic_format=use_anthropic_format,
        max_context_tokens=max_context_tokens,
        max_source_reads=0,
        include_write_artifact=True,
        phase_label="architect",
    )

    missing_target_artifacts = _missing_target_state_artifacts(artifacts_dir, selected_repo_archetype)
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
    # Print architect tool usage alongside analyst counts
    arch_counts = arch_result.get("tool_call_counts", {})
    if arch_counts:
        _log.print_tool_usage_table({"architect": arch_counts})

    if arch_result["status"] == "failed":
        _log.update_workflow_phase("architect", status="failed", tool="error")
        _log.stop_agent_boxes()
        return {
            "status": "failed",
            "error": f"architect failed: {arch_result['error']}",
            "notes": "",
            "artifacts": all_artifacts,
            "events": all_events,
            "tool_uses": total_tool_uses,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "prompt_archetype": selected_repo_archetype,
            "artifact_plan": plan,
            "artifact_omissions": [],
            "phase_tokens": phase_tokens,
        }
    _log.update_workflow_phase("architect", status="done", tool="target-state complete")

    artifact_omissions = _conditional_artifact_omissions(artifacts_dir, plan)
    _log.update_workflow_phase("summary", status="running", tool="executive summary")
    summary_result = _run_executive_summary_phase(
        provider,
        artifacts_dir,
        repo_name,
        plan.get("primary_repo_type", selected_repo_archetype),
        list(plan.get("capabilities", [])),
        list((repo_metrics or {}).get("detected_language_categories", [])),
        plan,
        artifact_omissions,
        repo_metrics,
    )
    summary_path = summary_result["path"]
    total_input_tokens += summary_result["input_tokens"]
    total_output_tokens += summary_result["output_tokens"]
    phase_tokens["summary"] = {
        "input": summary_result["input_tokens"],
        "output": summary_result["output_tokens"],
        "total": summary_result["input_tokens"] + summary_result["output_tokens"],
    }
    all_artifacts.append(summary_path)
    all_events.extend(summary_result["events"])
    all_events.append(f"[supervisor] executive summary generated — {summary_path}")

    manifest_path = _write_machine_manifest(
        artifacts_dir,
        repo_name,
        selected_repo_archetype,
        artifact_plan=plan,
        artifact_omissions=artifact_omissions,
        repo_metrics=repo_metrics,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )
    all_artifacts.append(manifest_path)
    all_events.append(f"[supervisor] manifest generated — {manifest_path}")
    _log.update_workflow_phase("summary", status="done", tool="summary and manifest complete")
    _log.stop_agent_boxes()

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
        "prompt_archetype": selected_repo_archetype,
        "artifact_plan": plan,
        "artifact_omissions": artifact_omissions,
        "phase_tokens": phase_tokens,
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
    selected_archetype = ps.selected_archetype if not is_dict else ps.get("selected_archetype", "")
    primary_repo_type = ps.primary_repo_type if not is_dict else ps.get("primary_repo_type", selected_archetype)
    artifact_plan = ps.artifact_plan if not is_dict else ps.get("artifact_plan")

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
        repo_archetype=primary_repo_type or selected_archetype,
        artifact_plan=artifact_plan,
    )

    events.extend(result["events"])

    if not is_dict:
        ps.events = events
        ps.input_tokens = result["input_tokens"]
        ps.output_tokens = result["output_tokens"]
        ps.tool_uses = result["tool_uses"]
        ps.phase_tokens = result.get("phase_tokens", {})
        ps.prompt_archetype = result.get("prompt_archetype", selected_archetype)
        ps.primary_repo_type = primary_repo_type or ps.prompt_archetype
        ps.artifact_plan = result.get("artifact_plan", artifact_plan)
        ps.artifact_omissions = result.get("artifact_omissions", [])
        if result["status"] == "failed":
            ps.status = "failed"
            ps.error = result["error"]
        else:
            ps.artifacts_dir = artifacts_dir
            if not result["artifacts"]:
                ps.status = "failed"
                ps.error = "agent completed but wrote no artifacts"
            else:
                active_plan = ps.artifact_plan or build_artifact_plan(ps.primary_repo_type or ps.prompt_archetype, ps.repo_metrics)
                missing_artifacts = validate_artifacts(
                    artifacts_dir,
                    archetype=ps.prompt_archetype or "backend-service",
                    artifact_plan=active_plan,
                )
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
                    if ps.primary_repo_type:
                        ps.log("agent", f"primary repo type: {ps.primary_repo_type}")
                    if ps.artifact_omissions:
                        ps.log("agent", f"conditional omissions: {len(ps.artifact_omissions)}")
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
        active_plan = result.get("artifact_plan") or artifact_plan or build_artifact_plan(primary_repo_type or archetype, ps.get("repo_metrics") if is_dict else None)
        missing_artifacts = validate_artifacts(artifacts_dir, archetype=archetype, artifact_plan=active_plan)
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
            "primary_repo_type": primary_repo_type or result.get("prompt_archetype", ""),
            "artifact_plan": active_plan,
            "artifact_omissions": result.get("artifact_omissions", []),
            "phase_tokens": result.get("phase_tokens", {}),
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
