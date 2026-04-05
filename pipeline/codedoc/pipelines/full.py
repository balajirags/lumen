"""Full Lumen pipeline orchestration."""

from __future__ import annotations

import time
from pathlib import Path

from codedoc.preflight import run_preflights
from codedoc.pipelines.common import create_run_dir, finalize_state, init_state, log_pipeline_start
from codedoc.stages.agent import run_agent
from codedoc.stages.builder import run_builder
from codedoc.stages.indexer import run_indexer
from codedoc.state import PipelineState


def run_pipeline(
    repo_path: str,
    output_dir: str,
    model: str,
    provider: str,
    base_url: str,
    max_turns: int,
    max_context_tokens: int = 120_000,
    timeout: int = 300,
    repo_size_check: str = "warn",
    verbose: bool = False,
    indexer_bin_dir: str = "",
    agent_prompt: str = "",
    build_script: str = "",
    repo_name: str | None = None,
) -> PipelineState:
    """Execute the full pipeline and return final state."""
    repo = Path(repo_path).resolve()
    resolved_repo_name = repo_name or repo.name
    run_dir = create_run_dir(output_dir, resolved_repo_name)

    state = init_state(
        repo_path=str(repo),
        run_dir=run_dir,
        mode="full",
        model=model,
        provider=provider,
        base_url=base_url,
        max_turns=max_turns,
        max_context_tokens=max_context_tokens,
        timeout=timeout,
        repo_size_check=repo_size_check,
        verbose=verbose,
        indexer_bin_dir=indexer_bin_dir,
        agent_prompt=agent_prompt,
        build_script=build_script,
        site_dir=str(Path(output_dir) / "doc-site"),
    )
    log_pipeline_start(state, repo_path=str(repo), run_dir=run_dir, label="pipeline")

    from codedoc import log as _log

    pipeline_t0 = time.time()
    try:
        state.log("pipeline", "=== Preflight: Repo Metrics ===")
        state = run_preflights(state)
        if state.status == "failed":
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

        state.log("pipeline", "=== Stage 2: Agent ===")
        _log.print_stage_header(2, "Agent")
        t0 = time.time()
        state = run_agent(state)
        elapsed2 = time.time() - t0
        state.log("pipeline", f"Stage 2 (Agent) completed in {elapsed2:.1f}s")
        _log.print_stage_done(2, "Agent", elapsed2)
        if state.status == "failed":
            return state

        state.log("pipeline", "=== Stage 3: Builder ===")
        _log.print_stage_header(3, "Builder")
        t0 = time.time()
        state = run_builder(state)
        elapsed3 = time.time() - t0
        state.log("pipeline", f"Stage 3 (Builder) completed in {elapsed3:.1f}s")
        _log.print_stage_done(3, "Builder", elapsed3)

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

