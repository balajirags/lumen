"""Security-audit Lumen pipeline orchestration.

Example of a pluggable pipeline: identical preflight/indexer sequencing to
``pipelines/full.py``, reusing the exact same shared stages unchanged, but Stage 2 runs
``stages/security_audit_agent.run_agent`` (fan-out access/dependency/threat-model reviewers
+ fan-in risk synthesis) instead of the docs pipeline's Analyst+Architect supervisor. There is no
Stage 3 builder step — artifacts are left as plain markdown under ``artifacts/security/``.
"""

from __future__ import annotations

import time
from pathlib import Path

from codedoc.preflight import run_preflights
from codedoc.pipelines.common import (
    apply_repo_size_runtime_defaults,
    create_run_dir,
    finalize_state,
    init_state,
    log_pipeline_start,
    should_stop_for_xlarge_repo,
)
from codedoc.stages.indexer import run_indexer
from codedoc.stages.security_audit_agent import run_agent
from codedoc.state import PipelineState


def run_pipeline(
    repo_path: str,
    output_dir: str,
    model: str,
    provider: str,
    base_url: str,
    max_turns: int,
    max_context_tokens: int = 120_000,
    ollama_num_ctx: int = 131_072,
    timeout: int = 300,
    repo_size_check: str = "warn",
    allow_xlarge: bool = False,
    timeout_explicit: bool = False,
    max_turns_explicit: bool = False,
    verbose: bool = False,
    indexer_bin_dir: str = "",
    repo_name: str | None = None,
) -> PipelineState:
    """Execute the security-audit pipeline and return final state."""
    repo = Path(repo_path).resolve()
    resolved_repo_name = repo_name or repo.name
    run_dir = create_run_dir(output_dir, resolved_repo_name)

    state = init_state(
        repo_path=str(repo),
        run_dir=run_dir,
        mode="security-audit",
        model=model,
        provider=provider,
        base_url=base_url,
        max_turns=max_turns,
        max_context_tokens=max_context_tokens,
        ollama_num_ctx=ollama_num_ctx,
        timeout=timeout,
        repo_size_check=repo_size_check,
        allow_xlarge=allow_xlarge,
        verbose=verbose,
        timeout_explicit=timeout_explicit,
        max_turns_explicit=max_turns_explicit,
        indexer_bin_dir=indexer_bin_dir,
        repo_name=resolved_repo_name,
    )
    log_pipeline_start(state, repo_path=str(repo), run_dir=run_dir, label="security-audit pipeline")

    from codedoc import log as _log

    pipeline_t0 = time.time()
    try:
        state.log("pipeline", "=== Preflight: Repo Metrics ===")
        state = run_preflights(state)
        if state.status == "failed":
            return state
        state = apply_repo_size_runtime_defaults(state, bump_max_turns=True)
        if state.allow_xlarge and str((state.repo_metrics or {}).get("size_band", "")) == "xlarge":
            state.log(
                "pipeline",
                "Repo classified as xlarge, but --allow-xlarge is set. Continuing with security-audit pipeline.",
            )
        if should_stop_for_xlarge_repo(state):
            state.status = "stopped"
            state.error = (
                "Repo classified as xlarge; security-audit pipeline stopped before indexing. "
                "Rerun with --allow-xlarge to continue anyway."
            )
            state.log(
                "pipeline",
                "Repo classified as xlarge. Stopping security-audit pipeline before indexing.",
            )
            return state

        state.log("pipeline", "=== Stage 1: Indexer ===")
        _log.print_stage_header(1, "Indexer")
        t0 = time.time()
        state = run_indexer(state)
        elapsed1 = time.time() - t0
        state.log("pipeline", f"Stage 1 (Indexer) completed in {elapsed1:.1f}s")
        _log.print_stage_done(1, "Indexer", elapsed1)
        if state.status == "failed":
            return state

        state.log("pipeline", "=== Stage 2: Security Audit Agent ===")
        _log.print_stage_header(2, "Agent")
        t0 = time.time()
        state = run_agent(state)
        elapsed2 = time.time() - t0
        state.log("pipeline", f"Stage 2 (Agent) completed in {elapsed2:.1f}s")
        _log.print_stage_done(2, "Agent", elapsed2)
        if state.status == "failed":
            return state

        state.status = "done"
        pipeline_elapsed = time.time() - pipeline_t0
        state.log("pipeline", f"Pipeline completed successfully in {pipeline_elapsed:.1f}s")
    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        state.log("pipeline", f"Pipeline failed: {exc}")
    finally:
        finalize_state(state, run_dir)

    return state
