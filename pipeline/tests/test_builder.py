from __future__ import annotations

from pathlib import Path

from codedoc.state import PipelineState
from codedoc.stages.builder import run_builder


def test_run_builder_falls_back_without_script(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "tech").mkdir(parents=True)
    (artifacts_dir / "tech" / "coupling-hotspots.md").write_text("# Hotspots")

    state = PipelineState(
        repo_path=str(tmp_path / "repo"),
        output_dir=str(tmp_path / "output" / "repo-123"),
        artifacts_dir=str(artifacts_dir),
        build_script=str(tmp_path / "missing-build.sh"),
        site_dir=str(tmp_path / "site"),
        status="running",
    )

    result = run_builder(state)

    assert Path(result.site_path, "index.html").exists()
    html = Path(result.site_path, "index.html").read_text()
    assert "lumen Artifacts" in html
