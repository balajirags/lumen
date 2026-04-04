from __future__ import annotations

from pathlib import Path

from codedoc.stages.agent import (
    _select_repo_archetype,
    validate_artifacts,
)


class FakeBackend:
    def __init__(self, counts):
        self.counts = counts

    def execute(self, query: str):
        for key, value in self.counts.items():
            if key in query:
                return [{"c": value}]
        return [{"c": 0}]


def test_select_repo_archetype_frontend():
    backend = FakeBackend(
        {
            "MATCH (n:Component)": 4,
            "MATCH (n:Hook)": 2,
            "MATCH ()-[r:RENDERS]": 6,
            "RestController": 0,
            "route|endpoint|handler|controller|api|get_|post_|put_|delete_": 0,
            "MATCH (n:Method)": 0,
            "MATCH (n:Function)": 10,
            "MATCH (n:Package)": 0,
        }
    )

    assert _select_repo_archetype(backend) == "frontend-app"


def test_validate_artifacts_reports_missing(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "domain").mkdir(parents=True)
    (artifacts_dir / "domain" / "business-capabilities.md").write_text("ok")

    missing = validate_artifacts(str(artifacts_dir), ["domain/business-capabilities.md", "tech/coupling-hotspots.md"])

    assert missing == ["tech/coupling-hotspots.md"]
