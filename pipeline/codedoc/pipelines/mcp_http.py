"""HTTP MCP pipeline orchestration."""

from __future__ import annotations

import time
from pathlib import Path

from codedoc.mcp_server import format_mcp_http_command, format_mcp_http_url
from codedoc.preflight import run_preflights
from codedoc.pipelines.common import (
    apply_repo_size_runtime_defaults,
    create_run_dir,
    finalize_state,
    init_state,
    log_pipeline_start,
)
from codedoc.stages.indexer import run_indexer
from codedoc.state import PipelineState


def run_mcp_http_pipeline(
    repo_path: str,
    output_dir: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    path: str = "/mcp",
    timeout: int = 300,
    timeout_explicit: bool = False,
    repo_size_check: str = "warn",
    verbose: bool = False,
    indexer_bin_dir: str = "",
    repo_name: str | None = None,
) -> PipelineState:
    """Execute the preflight + indexer path and prepare HTTP MCP metadata."""
    repo = Path(repo_path).resolve()
    resolved_repo_name = repo_name or repo.name
    run_dir = create_run_dir(output_dir, resolved_repo_name)

    state = init_state(
        repo_path=str(repo),
        run_dir=run_dir,
        mode="mcp-http",
        timeout=timeout,
        repo_size_check=repo_size_check,
        verbose=verbose,
        timeout_explicit=timeout_explicit,
        indexer_bin_dir=indexer_bin_dir,
        repo_name=resolved_repo_name,
    )
    log_pipeline_start(state, repo_path=str(repo), run_dir=run_dir, label="MCP HTTP pipeline")

    from codedoc import log as _log

    pipeline_t0 = time.time()
    try:
        state.log("pipeline", "=== Preflight: Repo Metrics ===")
        state = run_preflights(state)
        if state.status == "failed":
            return state
        state = apply_repo_size_runtime_defaults(state)

        state.log("pipeline", "=== Stage 1: Indexer ===")
        _log.print_stage_header(1, "Indexer")
        t0 = time.time()
        state = run_indexer(state)
        elapsed = time.time() - t0
        state.log("pipeline", f"Stage 1 (Indexer) completed in {elapsed:.1f}s")
        _log.print_stage_done(1, "Indexer", elapsed)
        if state.status == "failed":
            return state

        state.mcp_command = format_mcp_http_command(
            state.kuzu_path,
            repo_path=state.repo_path,
            host=host,
            port=port,
            path=path,
        )
        state.mcp_url = format_mcp_http_url(host=host, port=port, path=path)
        state.status = "done"
        pipeline_elapsed = time.time() - pipeline_t0
        state.log("pipeline", f"MCP HTTP pipeline completed successfully in {pipeline_elapsed:.1f}s")
    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        state.log("pipeline", f"MCP HTTP pipeline failed: {exc}")
    finally:
        finalize_state(state, run_dir)

    return state
