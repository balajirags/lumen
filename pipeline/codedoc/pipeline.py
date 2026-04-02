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
) -> PipelineState:
    """Execute the full pipeline and return final state.

    Creates a timestamped output directory, runs all three stages in sequence,
    writes pipeline.json, and returns the state.
    """
    repo = Path(repo_path).resolve()
    repo_name = repo.name

    # Timestamped output dir
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(output_dir) / f"{repo_name}-{ts}"
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
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    state.log("pipeline", f"Starting pipeline for {repo}")
    state.log("pipeline", f"Output directory: {run_dir}")

    pipeline_t0 = time.time()

    try:
        # Stage 1: Indexer
        state.log("pipeline", "=== Stage 1: Indexer ===")
        t0 = time.time()
        state = run_indexer(state)
        state.log("pipeline", f"Stage 1 (Indexer) completed in {time.time() - t0:.1f}s")

        if state.status == "failed":
            return state

        # Resolve KuzuDB directory → file if needed
        kuzu_path = Path(state.kuzu_path)
        if kuzu_path.is_dir():
            db_files = [f for f in kuzu_path.iterdir() if f.is_file()]
            if not db_files:
                raise FileNotFoundError(f"No KuzuDB file found in directory: {kuzu_path}")
            state.kuzu_path = str(db_files[0])
            state.log("pipeline", f"Resolved KuzuDB file: {state.kuzu_path}")

        # Stage 2: Agent
        state.log("pipeline", "=== Stage 2: Agent ===")
        t0 = time.time()
        state = run_agent(state)
        state.log("pipeline", f"Stage 2 (Agent) completed in {time.time() - t0:.1f}s")

        if state.status == "failed":
            return state

        # Stage 3: Builder
        state.log("pipeline", "=== Stage 3: Builder ===")
        t0 = time.time()
        state = run_builder(state)
        state.log("pipeline", f"Stage 3 (Builder) completed in {time.time() - t0:.1f}s")

        state.status = "done"
        pipeline_elapsed = time.time() - pipeline_t0
        state.log("pipeline", f"Pipeline completed successfully in {pipeline_elapsed:.1f}s")

    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        state.log("pipeline", f"Pipeline failed: {exc}")
        print("\n--- Last events before failure ---")
        for line in state.events[-10:]:
            print(f"  {line}")
        print(f"\nERROR: {exc}\n")
    finally:
        state.finished_at = datetime.now(timezone.utc).isoformat()
        pipeline_json = run_dir / "pipeline.json"
        pipeline_json.write_text(
            json.dumps(state.to_pipeline_json(), indent=2, default=str)
        )
        state.log("pipeline", f"Wrote {pipeline_json}")

    return state
