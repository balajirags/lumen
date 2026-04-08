from __future__ import annotations

from codedoc.state import PipelineState


def test_to_pipeline_json_persists_analysis_runtime_fields():
    state = PipelineState(
        repo_path="/tmp/repo",
        model="claude-sonnet-4-6",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        timeout=600,
        max_turns=90,
        max_context_tokens=150_000,
    )

    payload = state.to_pipeline_json()

    assert payload["runtime"]["model"] == "claude-sonnet-4-6"
    assert payload["runtime"]["provider"] == "anthropic"
    assert payload["runtime"]["base_url"] == "https://api.anthropic.com"
    assert payload["runtime"]["timeout"] == 600
    assert payload["runtime"]["max_turns"] == 90
    assert payload["runtime"]["max_context_tokens"] == 150_000
