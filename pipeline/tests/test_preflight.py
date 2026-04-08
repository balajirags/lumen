from __future__ import annotations

from codedoc.preflight.repo_metrics import classify_repo_metrics, collect_repo_metrics
from codedoc.preflight.runner import run_preflights
from codedoc.state import PipelineState


def test_collect_repo_metrics_counts_loc_and_languages(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("class App {\n}\n")
    (tmp_path / "src" / "api.ts").write_text("export const x = 1;\n\nexport const y = 2;\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("console.log('ignore')\n")

    metrics = collect_repo_metrics(str(tmp_path))

    assert metrics["total_source_files"] == 2
    assert metrics["total_loc"] == 4
    assert metrics["files_by_language"] == {"java": 1, "js": 1}
    assert metrics["detected_languages"] == ["java", "js"]
    assert metrics["files_by_category"] == {"jvm": 1, "js": 1}
    assert metrics["detected_language_categories"] == ["jvm", "js"]
    assert metrics["selected_archetype"] == "backend-service"


def test_classify_repo_metrics_escalates_risk():
    classified = classify_repo_metrics(
        {
            "total_loc": 60_000,
            "total_source_files": 1_100,
            "detected_language_categories": ["jvm", "js", "python"],
        },
        max_turns=60,
        max_context_tokens=120_000,
    )

    assert classified["size_band"] == "large"
    assert classified["risk_level"] in {"critical", "high"}
    assert classified["guardrail_triggered"] is True


def test_run_preflights_off_leaves_state_untouched(tmp_path):
    state = PipelineState(
        repo_path=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        repo_size_check="off",
        status="running",
    )

    result = run_preflights(state)

    assert result.status == "running"
    assert result.repo_metrics is None


def test_run_preflights_strict_keeps_repo_metrics_informational(tmp_path):
    (tmp_path / "src").mkdir()
    large_file = tmp_path / "src" / "big.py"
    large_file.write_text("\n".join(f"line_{i}" for i in range(60_001)))

    state = PipelineState(
        repo_path=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        repo_size_check="strict",
        max_turns=60,
        max_context_tokens=120_000,
        status="running",
    )

    result = run_preflights(state)

    assert result.status == "running"
    assert result.repo_metrics is not None
    assert result.repo_metrics["guardrail_triggered"] is True


def test_run_preflights_registry_can_be_removed(monkeypatch, tmp_path):
    from codedoc.preflight import runner

    monkeypatch.setattr(runner, "_PREFLIGHTS", [])
    state = PipelineState(
        repo_path=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        repo_size_check="warn",
        status="running",
    )

    result = runner.run_preflights(state)

    assert result.status == "running"
    assert result.repo_metrics is None
