from __future__ import annotations

from codedoc import log as _log
from codedoc.preflight.repo_metrics import RepoMetricsPreflight


_PREFLIGHTS = [RepoMetricsPreflight()]


def run_preflights(state):
    for preflight in _PREFLIGHTS:
        if not preflight.enabled(state):
            continue
        result = preflight.run(
            state.repo_path,
            {
                "max_turns": state.max_turns,
                "max_context_tokens": state.max_context_tokens,
                "repo_size_check": state.repo_size_check,
            },
        )
        if preflight.name == "repo_metrics":
            state.repo_metrics = result.metadata
            _log.print_repo_metrics_panel(result.metadata, state.repo_size_check)
        state.log("pipeline", f"Preflight {preflight.name}: {result.summary}")
        for warning in result.warnings:
            state.log("pipeline", f"WARNING: {warning}")
        if result.should_block:
            state.status = "failed"
            state.error = result.warnings[0] if result.warnings else f"Preflight {preflight.name} blocked the run."
            return state
    return state
