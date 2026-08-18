"""Stage 2 (alternative) — Security audit agent.

Example of a pluggable agent stage: reuses the same low-level primitives as
``codedoc.stages.agent`` (KuzuBackend, ReverseEngineerToolkit, create_provider, run_loop)
against the graph produced by the unchanged preflight/indexer stages, but runs its own
fan-out/fan-in orchestration — 2 parallel reviewers, then 1 risk-synthesis pass — instead
of the docs pipeline's Analyst+Architect pattern.

Usage (as pipeline stage)::

    from codedoc.stages.security_audit_agent import run_agent
    state = run_agent(state)   # PipelineState in, PipelineState out
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codedoc.kg_tools import KuzuBackend, ReverseEngineerToolkit
from codedoc.llm import create_provider
from codedoc.stages.agent import run_loop
from codedoc.stages.parallel import run_parallel_tasks

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_ACCESS_ARTIFACT = "security/access-control-findings.md"
_DEPENDENCY_ARTIFACT = "security/dependency-risk-findings.md"
_REPORT_ARTIFACT = "security/audit-report.md"

_REVIEWER_PROMPT_FILES: dict[str, str] = {
    "security/access": "security-analyst-access.md",
    "security/dependencies": "security-analyst-dependencies.md",
}
_REVIEWER_ARTIFACTS: dict[str, set[str]] = {
    "security/access": {_ACCESS_ARTIFACT},
    "security/dependencies": {_DEPENDENCY_ARTIFACT},
}


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def _orientation_summary(kuzu_path: str, repo_path: str) -> str:
    backend = KuzuBackend(kuzu_path)
    toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
    try:
        return toolkit.call("get_architecture_summary")
    except Exception as e:
        return (
            f"[Orientation data unavailable — graph query failed ({e}). "
            "Use get_schema and get_architecture_overview to orient yourself.]"
        )


def _make_reviewer_task(
    name: str,
    *,
    provider,
    kuzu_path: str,
    repo_path: str,
    repo_name: str,
    orientation_summary: str,
    artifacts_dir: str,
    max_turns: int,
    max_context_tokens: int,
    use_anthropic_format: bool,
    verbose: bool,
):
    def _task() -> dict:
        backend = KuzuBackend(kuzu_path)
        toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
        system_prompt = (
            _load_prompt(_REVIEWER_PROMPT_FILES[name])
            + f"\n\n---\n## Orientation Summary\n\n{orientation_summary}\n"
            + f"\n\n---\n## Runtime context\n\n- Repository name: `{repo_name}`\n"
        )
        return run_loop(
            provider=provider,
            toolkit=toolkit,
            system_prompt=system_prompt,
            user_request="Begin your review and write your findings artifact.",
            output_root=artifacts_dir,
            max_turns=max_turns,
            verbose=verbose,
            use_anthropic_format=use_anthropic_format,
            max_context_tokens=max_context_tokens,
            max_source_reads=0,
            include_write_artifact=True,
            phase_label=name,
            allowed_artifact_paths=_REVIEWER_ARTIFACTS[name],
        )

    return _task


def _build_synthesis_prompt(artifacts_dir: str, repo_name: str) -> str:
    prompt = _load_prompt("security-synthesis.md")
    prompt += "\n\n---\n## Findings (written by reviewers)\n\n"
    for rel_path in (_ACCESS_ARTIFACT, _DEPENDENCY_ARTIFACT):
        full_path = Path(artifacts_dir) / rel_path
        if full_path.exists():
            prompt += f"### {rel_path}\n\n{full_path.read_text(encoding='utf-8')}\n\n"
        else:
            prompt += f"### {rel_path}\n\n_Not produced by reviewer._\n\n"
    prompt += (
        "\n\n---\n## Runtime context\n\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Write only `{_REPORT_ARTIFACT}` using the `write_artifact` tool.\n"
        "- Do NOT call any graph query tools.\n"
    )
    return prompt


def run_agent(state) -> Any:
    """Pipeline stage 2 for the security-audit pipeline.

    Reads: kuzu_path, repo_path, output_dir, model, provider, base_url, max_turns,
           max_context_tokens, ollama_num_ctx, verbose, repo_name.
    Writes: artifacts_dir, events, input_tokens, output_tokens, tool_uses, status/error.
    """
    kuzu_path = state.kuzu_path
    repo_path = state.repo_path
    output_dir = state.output_dir
    model = state.model
    provider_name = state.provider
    base_url = state.base_url
    max_turns = state.max_turns
    max_context_tokens = state.max_context_tokens
    ollama_num_ctx = state.ollama_num_ctx or 131_072
    verbose = state.verbose
    repo_name = state.repo_name or (Path(repo_path).name if repo_path else "unknown")

    try:
        KuzuBackend(kuzu_path)
    except FileNotFoundError as e:
        state.status = "failed"
        state.error = str(e)
        return state

    provider = create_provider(provider=provider_name, model=model, base_url=base_url, num_ctx=ollama_num_ctx)
    use_anthropic_format = provider_name in ("claude", "anthropic") or (
        provider_name == "auto" and (model.startswith("claude") or model.startswith("anthropic"))
    )

    artifacts_dir = str(Path(output_dir) / "artifacts")
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    events: list[str] = []
    input_tokens = 0
    output_tokens = 0
    tool_uses = 0

    from codedoc import log as _log

    def log(msg: str) -> None:
        events.append(msg)
        _log.print_supervisor_line(msg)

    # ------------------------------------------------------------------
    # Phase 1: direct toolkit query — no LLM needed
    # ------------------------------------------------------------------
    log("[security-audit] Phase 1 — orientation (direct toolkit query)")
    orientation_summary = _orientation_summary(kuzu_path, repo_path)

    # ------------------------------------------------------------------
    # Phase 2: fan-out — 2 parallel reviewers
    # ------------------------------------------------------------------
    reviewer_turns = max(10, max_turns // 3)
    synthesis_turns = max(6, max_turns // 4)
    role_names = list(_REVIEWER_PROMPT_FILES)

    log("[security-audit] Phase 2 — spawning 2 reviewers in parallel (access · dependencies)")
    _log.start_agent_boxes(agent_names=role_names, workflow_phases=["security/synthesis"])
    for name in role_names:
        _log.update_agent_box(name, status="running", tool="starting")

    tasks = {
        name: _make_reviewer_task(
            name,
            provider=provider,
            kuzu_path=kuzu_path,
            repo_path=repo_path,
            repo_name=repo_name,
            orientation_summary=orientation_summary,
            artifacts_dir=artifacts_dir,
            max_turns=reviewer_turns,
            max_context_tokens=max_context_tokens,
            use_anthropic_format=use_anthropic_format,
            verbose=verbose,
        )
        for name in role_names
    }
    try:
        results = run_parallel_tasks(tasks)
    except Exception:
        _log.stop_agent_boxes()
        raise

    all_artifacts: list[str] = []
    reviewer_tool_counts: dict[str, dict[str, int]] = {}
    for name, result in results.items():
        events.extend(result["events"])
        input_tokens += result["input_tokens"]
        output_tokens += result["output_tokens"]
        tool_uses += result["tool_uses"]
        reviewer_tool_counts[name] = result.get("tool_call_counts", {})
        n_artifacts = len(result["artifacts"])
        if result["status"] == "failed":
            _log.update_agent_box(name, status="failed", tool="error")
            log(f"[security-audit] WARNING: {name} failed: {result['error']}")
        else:
            all_artifacts.extend(result["artifacts"])
            _log.update_agent_box(name, status="done", tool="complete", artifacts=n_artifacts)
            log(f"[security-audit] {name} done — {n_artifacts} artifact(s)")
        _log.print_researcher_done(name, n_artifacts)
    _log.print_tool_usage_table(reviewer_tool_counts)

    # ------------------------------------------------------------------
    # Phase 3: fan-in — risk synthesis
    # ------------------------------------------------------------------
    log("[security-audit] Phase 3 — running risk synthesis…")
    _log.update_workflow_phase("security/synthesis", status="running", tool="risk synthesis")
    synthesis_backend = KuzuBackend(kuzu_path)
    synthesis_toolkit = ReverseEngineerToolkit(synthesis_backend, repo_path=repo_path)
    try:
        synthesis_result = run_loop(
            provider=provider,
            toolkit=synthesis_toolkit,
            system_prompt=_build_synthesis_prompt(artifacts_dir, repo_name),
            user_request="Write the prioritized audit report now.",
            output_root=artifacts_dir,
            max_turns=synthesis_turns,
            verbose=verbose,
            use_anthropic_format=use_anthropic_format,
            max_context_tokens=max_context_tokens,
            max_source_reads=0,
            include_write_artifact=True,
            phase_label="security/synthesis",
            allowed_artifact_paths={_REPORT_ARTIFACT},
        )
    except Exception:
        _log.update_workflow_phase("security/synthesis", status="failed", tool="error")
        _log.stop_agent_boxes()
        raise
    events.extend(synthesis_result["events"])
    input_tokens += synthesis_result["input_tokens"]
    output_tokens += synthesis_result["output_tokens"]
    tool_uses += synthesis_result["tool_uses"]

    state.events = list(state.events) + events
    state.input_tokens = input_tokens
    state.output_tokens = output_tokens
    state.tool_uses = tool_uses

    if synthesis_result["status"] == "failed":
        _log.update_workflow_phase("security/synthesis", status="failed", tool="error")
        _log.stop_agent_boxes()
        state.status = "failed"
        state.error = synthesis_result["error"]
        return state

    _log.update_workflow_phase("security/synthesis", status="done", tool="report written")
    _log.print_synthesizer_done(len(synthesis_result["artifacts"]))
    _log.stop_agent_boxes()

    all_artifacts.extend(synthesis_result["artifacts"])
    if not all_artifacts:
        state.status = "failed"
        state.error = "security-audit agent completed but wrote no artifacts"
        return state

    state.artifacts_dir = artifacts_dir
    state.log("agent", f"{len(all_artifacts)} artifact(s) → {artifacts_dir}")
    state.log("agent", f"tool calls used: {tool_uses}")
    state.log(
        "agent",
        f"tokens — input: {input_tokens:,}  output: {output_tokens:,}  total: {input_tokens + output_tokens:,}",
    )
    return state
