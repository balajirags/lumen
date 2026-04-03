"""Pipeline orchestration.

Wires the three stages (indexer → agent → builder) into a simple sequential
function chain using shared PipelineState.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from codedoc.state import PipelineState
from codedoc.stages.indexer import run_indexer
from codedoc.stages.agent import run_agent
from codedoc.stages.builder import run_builder


def run_pipeline(
    repo_path: str,
    output_dir: str,
    model: str,
    provider: str,
    base_url: str,
    max_turns: int,
    max_context_tokens: int = 120_000,
    timeout: int = 300,
    verbose: bool = False,
    indexer_bin_dir: str = "",
    agent_prompt: str = "",
    build_script: str = "",
    repo_name: str | None = None,
) -> PipelineState:
    """Execute the full pipeline and return final state.

    Creates a timestamped output directory, runs all three stages in sequence,
    writes pipeline.json, and returns the state.
    """
    repo = Path(repo_path).resolve()
    repo_name = repo_name or repo.name

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(output_dir) / f"{repo_name[:20]}-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Initialize state
    state = PipelineState(
        repo_path=str(repo),
        output_dir=str(run_dir),
        model=model,
        provider=provider,
        base_url=base_url,
        max_turns=max_turns,
        max_context_tokens=max_context_tokens,
        timeout=timeout,
        verbose=verbose,
        indexer_bin_dir=indexer_bin_dir,
        agent_prompt=agent_prompt,
        build_script=build_script,
        site_dir=str(Path(output_dir) / "doc-site"),
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    from codedoc import log as _log
    _log.print_pipeline_start(str(repo), str(run_dir))
    state.log("pipeline", f"Starting pipeline for {repo}")
    state.log("pipeline", f"Output directory: {run_dir}")

    pipeline_t0 = time.time()

    try:
        # Stage 1: Indexer
        state.log("pipeline", "=== Stage 1: Indexer ===")
        _log.print_stage_header(1, "Indexer")
        t0 = time.time()
        state = run_indexer(state)
        elapsed1 = time.time() - t0
        state.log("pipeline", f"Stage 1 (Indexer) completed in {elapsed1:.1f}s")
        _log.print_stage_done(1, "Indexer", elapsed1)

        if state.status == "failed":
            return state

        # Stage 2: Agent
        state.log("pipeline", "=== Stage 2: Agent ===")
        _log.print_stage_header(2, "Agent")
        t0 = time.time()
        state = run_agent(state)
        elapsed2 = time.time() - t0
        state.log("pipeline", f"Stage 2 (Agent) completed in {elapsed2:.1f}s")
        _log.print_stage_done(2, "Agent", elapsed2)

        if state.status == "failed":
            return state

        # Stage 3: Builder
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
        state.finished_at = datetime.now(timezone.utc).isoformat()
        pipeline_json = run_dir / "pipeline.json"
        pipeline_json.write_text(
            json.dumps(state.to_pipeline_json(), indent=2, default=str)
        )
        state.log("pipeline", f"Wrote {pipeline_json}")

    return state
