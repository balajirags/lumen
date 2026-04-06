"""Shared helpers for pipeline orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from codedoc.state import PipelineState


def create_run_dir(output_dir: str, repo_name: str) -> Path:
    """Create and return a timestamped run directory."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(output_dir) / f"{repo_name[:20]}-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def init_state(
    *,
    repo_path: str,
    run_dir: Path,
    mode: str,
    model: str = "claude-sonnet-4-6",
    provider: str = "auto",
    base_url: str = "",
    max_turns: int = 60,
    max_context_tokens: int = 120_000,
    timeout: int = 300,
    repo_size_check: str = "warn",
    allow_xlarge: bool = False,
    verbose: bool = False,
    timeout_explicit: bool = False,
    max_turns_explicit: bool = False,
    indexer_bin_dir: str = "",
    agent_prompt: str = "",
    build_script: str = "",
    site_dir: str = "",
) -> PipelineState:
    """Build the initial state shared by all pipeline modes."""
    return PipelineState(
        repo_path=repo_path,
        output_dir=str(run_dir),
        model=model,
        provider=provider,
        base_url=base_url,
        max_turns=max_turns,
        max_context_tokens=max_context_tokens,
        timeout=timeout,
        repo_size_check=repo_size_check,
        allow_xlarge=allow_xlarge,
        verbose=verbose,
        timeout_explicit=timeout_explicit,
        max_turns_explicit=max_turns_explicit,
        timeout_source="explicit" if timeout_explicit else "default",
        max_turns_source="explicit" if max_turns_explicit else "default",
        indexer_bin_dir=indexer_bin_dir,
        agent_prompt=agent_prompt,
        build_script=build_script,
        site_dir=site_dir,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        mode=mode,
    )


def log_pipeline_start(state: PipelineState, *, repo_path: str, run_dir: Path, label: str) -> None:
    """Emit standard startup logs for a pipeline run."""
    from codedoc import log as _log

    _log.print_pipeline_start(repo_path, str(run_dir))
    state.log("pipeline", f"Starting {label} for {repo_path}")
    state.log("pipeline", f"Output directory: {run_dir}")


def finalize_state(state: PipelineState, run_dir: Path) -> None:
    """Persist pipeline metadata on completion."""
    state.finished_at = datetime.now(timezone.utc).isoformat()
    pipeline_json = run_dir / "pipeline.json"
    pipeline_json.write_text(
        json.dumps(state.to_pipeline_json(), indent=2, default=str)
    )
    state.log("pipeline", f"Wrote {pipeline_json}")


def should_stop_full_pipeline_for_xlarge_repo(state: PipelineState) -> bool:
    """Return True when the full pipeline should stop after preflight."""
    return (
        state.mode == "full"
        and bool(state.repo_metrics)
        and not state.allow_xlarge
        and str(state.repo_metrics.get("size_band", "")) == "xlarge"
    )


def apply_repo_size_runtime_defaults(state: PipelineState) -> PipelineState:
    """Adjust runtime defaults from repo size when the user did not override them."""
    size_band = str((state.repo_metrics or {}).get("size_band", ""))
    if size_band not in {"large", "xlarge"}:
        state.log(
            "pipeline",
            f"Effective runtime settings — timeout={state.timeout}s ({state.timeout_source}), "
            f"max_turns={state.max_turns} ({state.max_turns_source})",
        )
        return state

    if not state.timeout_explicit:
        state.timeout = 3600
        state.timeout_source = f"adaptive-{size_band}"

    if state.mode == "full" and not state.max_turns_explicit:
        state.max_turns = 100
        state.max_turns_source = f"adaptive-{size_band}"

    state.log(
        "pipeline",
        f"Effective runtime settings — timeout={state.timeout}s ({state.timeout_source}), "
        f"max_turns={state.max_turns} ({state.max_turns_source})",
    )
    return state
