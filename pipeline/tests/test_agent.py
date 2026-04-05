from __future__ import annotations

import json
from pathlib import Path

from codedoc.stages.agent import (
    _select_repo_archetype,
    _backfill_required_artifacts,
    _write_machine_manifest,
    validate_artifact_quality,
    validate_artifact_warnings,
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


def test_validate_artifacts_uses_frontend_contract(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    for rel_path in [
        "architecture/route-map.md",
        "architecture/component-boundaries.md",
        "architecture/user-journeys.md",
        "current-state/state-management.md",
        "current-state/data-fetching-and-api-clients.md",
        "current-state/module-dependency-map.md",
        "tech/coupling-hotspots.md",
        "target-state/frontend-boundaries.md",
        "target-state/c4-target.md",
        "target-state/migration-plan.md",
        "manifests/artifacts.json",
    ]:
        full_path = artifacts_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text("ok")

    assert validate_artifacts(str(artifacts_dir), archetype="frontend-app") == []


def test_validate_artifact_quality_rejects_backend_artifacts_for_frontend(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    full_path = artifacts_dir / "current-state" / "api-spec.yaml"
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text("openapi: 3.0.0\npaths: {}\n")

    issues = validate_artifact_quality(str(artifacts_dir), "frontend-app")

    assert "unexpected artifact for frontend-app: current-state/api-spec.yaml" in issues


def test_validate_artifact_warnings_flag_unfenced_plantuml(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    full_path = artifacts_dir / "architecture" / "c4-context.md"
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text("@startuml\nAlice -> Bob\n@enduml\n")

    warnings = validate_artifact_warnings(str(artifacts_dir))

    assert "architecture/c4-context.md: PlantUML blocks should be fenced with ```plantuml" in warnings


def test_write_machine_manifest_uses_runtime_repo_name(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    for rel_path in [
        "architecture/route-map.md",
        "architecture/component-boundaries.md",
        "architecture/user-journeys.md",
        "current-state/state-management.md",
        "current-state/data-fetching-and-api-clients.md",
        "current-state/module-dependency-map.md",
        "tech/coupling-hotspots.md",
        "target-state/frontend-boundaries.md",
        "target-state/c4-target.md",
        "target-state/migration-plan.md",
    ]:
        full_path = artifacts_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text("ok")

    manifest_path = _write_machine_manifest(str(artifacts_dir), "admin-frontend", "frontend-app")
    manifest = json.loads(Path(manifest_path).read_text())

    assert manifest["repo_name"] == "admin-frontend"
    assert manifest["archetype"] == "frontend-app"
    assert manifest["artifacts"][-1]["file"] == "target-state/migration-plan.md"


def test_backfill_required_artifacts_frontend_module_dependency_map(tmp_path, monkeypatch):
    artifacts_dir = tmp_path / "artifacts"

    class FakeToolkit:
        def __init__(self, backend, repo_path=""):
            pass

        def call(self, name: str):
            assert name == "get_module_dependency_map"
            return "=== MODULE DEPENDENCY MAP ==="

    monkeypatch.setattr("codedoc.stages.agent.KuzuBackend", lambda path: object())
    monkeypatch.setattr("codedoc.stages.agent.ReverseEngineerToolkit", FakeToolkit)

    generated = _backfill_required_artifacts("fake-db", "fake-repo", str(artifacts_dir), "frontend-app")

    assert generated
    content = (artifacts_dir / "current-state" / "module-dependency-map.md").read_text()
    assert "MODULE DEPENDENCY MAP" in content
