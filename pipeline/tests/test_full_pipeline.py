from __future__ import annotations

from codedoc.pipelines.full import run_pipeline
from codedoc.pipelines.mcp_http import run_mcp_http_pipeline


def test_full_pipeline_stops_on_xlarge_repo_before_indexing(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    called: dict[str, bool] = {"indexed": False, "agent": False, "builder": False}

    def fake_run_preflights(state):
        state.repo_metrics = {
            "total_loc": 230_710,
            "total_source_files": 1_290,
            "detected_languages": ["java", "js"],
            "size_band": "xlarge",
            "risk_level": "critical",
            "warning_message": "This repo is large relative to current analysis settings; results may be partial or slower than expected.",
            "guardrail_triggered": True,
        }
        return state

    def fake_run_indexer(state):
        called["indexed"] = True
        return state

    def fake_run_agent(state):
        called["agent"] = True
        return state

    def fake_run_builder(state):
        called["builder"] = True
        return state

    monkeypatch.setattr("codedoc.pipelines.full.run_preflights", fake_run_preflights)
    monkeypatch.setattr("codedoc.pipelines.full.run_indexer", fake_run_indexer)
    monkeypatch.setattr("codedoc.pipelines.full.run_agent", fake_run_agent)
    monkeypatch.setattr("codedoc.pipelines.full.run_builder", fake_run_builder)

    state = run_pipeline(
        repo_path=str(repo),
        output_dir=str(tmp_path / "output"),
        model="claude-sonnet-4-6",
        provider="anthropic",
        base_url="",
        max_turns=60,
    )

    assert state.status == "stopped"
    assert "Use MCP mode" in (state.error or "")
    assert called["indexed"] is False
    assert called["agent"] is False
    assert called["builder"] is False


def test_mcp_http_pipeline_still_indexes_xlarge_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    called: dict[str, bool] = {"indexed": False}

    def fake_run_preflights(state):
        state.repo_metrics = {
            "total_loc": 230_710,
            "total_source_files": 1_290,
            "detected_languages": ["java", "js"],
            "size_band": "xlarge",
            "risk_level": "critical",
            "warning_message": "This repo is large relative to current analysis settings; results may be partial or slower than expected.",
            "guardrail_triggered": True,
        }
        return state

    def fake_run_indexer(state):
        called["indexed"] = True
        state.kuzu_path = str(tmp_path / "repo-db")
        return state

    monkeypatch.setattr("codedoc.pipelines.mcp_http.run_preflights", fake_run_preflights)
    monkeypatch.setattr("codedoc.pipelines.mcp_http.run_indexer", fake_run_indexer)

    state = run_mcp_http_pipeline(
        repo_path=str(repo),
        output_dir=str(tmp_path / "output"),
    )

    assert state.status == "done"
    assert called["indexed"] is True
