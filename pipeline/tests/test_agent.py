from __future__ import annotations

import json
from pathlib import Path

from codedoc.diagrams import write_c4_artifact
from codedoc.artifact_planner import build_artifact_plan
from codedoc.stages.agent import (
    _backfill_required_artifacts,
    _build_analyst_system_prompt,
    _build_architect_prompt,
    _build_architect_request,
    _build_executive_summary_evidence,
    _missing_target_state_artifacts,
    _required_artifacts,
    _run_executive_summary_phase,
    _write_executive_summary,
    _write_machine_manifest,
    validate_artifact_quality,
    validate_artifact_warnings,
    validate_artifacts,
)


def test_fullstack_contract_includes_cross_stack_artifacts():
    required = _required_artifacts("fullstack-app")

    assert "domain/business-capabilities.md" in required
    assert "architecture/c4-context.md" in required
    assert "current-state/api-spec.yaml" in required
    assert "domain/er-diagram.md" in required
    assert "current-state/ui-to-api-interactions.md" in required
    assert "target-state/fullstack-boundaries.md" in required


def test_fullstack_route_map_becomes_conditional_with_weak_frontend_signal():
    plan = build_artifact_plan(
        "fullstack-app",
        {
            "archetype_signal_counts": {"frontend-ui": 1, "backend-api": 5},
            "size_band": "small",
        },
    )

    route_item = next(item for item in plan["artifacts"] if item["path"] == "architecture/route-map.md")

    assert route_item["class"] == "conditional"
    assert route_item["required"] is False


def test_fullstack_route_map_stays_required_with_strong_frontend_signal():
    plan = build_artifact_plan(
        "fullstack-app",
        {
            "archetype_signal_counts": {"frontend-ui": 4, "backend-api": 5},
            "size_band": "small",
        },
    )

    route_item = next(item for item in plan["artifacts"] if item["path"] == "architecture/route-map.md")

    assert route_item["class"] == "core"
    assert route_item["required"] is True


def test_backend_contract_treats_api_spec_as_required():
    required = _required_artifacts("backend-service")

    assert "current-state/api-spec.yaml" in required
    assert "domain/er-diagram.md" in required


def test_validate_artifacts_reports_missing(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "domain").mkdir(parents=True)
    (artifacts_dir / "domain" / "business-capabilities.md").write_text("ok")

    missing = validate_artifacts(str(artifacts_dir), ["domain/business-capabilities.md", "tech/coupling-hotspots.md"])

    assert missing == ["tech/coupling-hotspots.md"]


def test_validate_artifacts_uses_frontend_contract(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    for rel_path in [
        "summary/executive-summary.md",
        "architecture/route-map.md",
        "architecture/component-boundaries.md",
        "architecture/user-journeys.md",
        "current-state/ui-to-api-interactions.md",
        "current-state/state-management.md",
        "current-state/data-fetching-and-api-clients.md",
        "current-state/module-dependency-map.md",
        "tech/coupling-hotspots.md",
        "target-state/frontend-boundaries.md",
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
        "target-state/migration-plan.md",
    ]:
        full_path = artifacts_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text("ok")

    manifest_path = _write_machine_manifest(
        str(artifacts_dir),
        "admin-frontend",
        "frontend-app",
        repo_metrics={
            "total_loc": 12_345,
            "total_source_files": 87,
            "size_band": "medium",
            "risk_level": "medium",
        },
        input_tokens=111,
        output_tokens=222,
    )
    manifest = json.loads(Path(manifest_path).read_text())

    assert manifest["repo_name"] == "admin-frontend"
    assert manifest["archetype"] == "frontend-app"
    assert manifest["repo_metrics"]["loc"] == 12_345
    assert manifest["tokens"] == {"input": 111, "output": 222, "total": 333}
    assert manifest["artifacts"][-1]["file"] == "target-state/migration-plan.md"


def test_write_machine_manifest_deduplicates_omissions(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    full_path = artifacts_dir / "summary" / "executive-summary.md"
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text("ok")

    manifest_path = _write_machine_manifest(
        str(artifacts_dir),
        "inventory-service",
        "fullstack-app",
        artifact_omissions=[
            {
                "file": "current-state/api-spec.yaml",
                "reason": "Conditional artifact omitted because evidence was weak.",
            }
        ],
    )
    manifest = json.loads(Path(manifest_path).read_text())

    omitted = [item for item in manifest["omitted"] if item["file"] == "current-state/api-spec.yaml"]
    assert len(omitted) == 1


def test_backfill_required_artifacts_frontend_module_dependency_map(tmp_path, monkeypatch):
    artifacts_dir = tmp_path / "artifacts"

    class FakeToolkit:
        def __init__(self, backend, repo_path=""):
            pass

        def call(self, name: str):
            if name == "get_module_dependency_map":
                return "=== MODULE DEPENDENCY MAP ==="
            if name == "get_route_map":
                return "=== ROUTE MAP ==="
            if name == "get_api_client_summary":
                return "=== API CLIENT SUMMARY ==="
            if name == "get_api_endpoints":
                return "=== API ENDPOINTS ==="
            raise AssertionError(name)

    monkeypatch.setattr("codedoc.stages.agent.KuzuBackend", lambda path: object())
    monkeypatch.setattr("codedoc.stages.agent.ReverseEngineerToolkit", FakeToolkit)

    generated = _backfill_required_artifacts("fake-db", "fake-repo", str(artifacts_dir), "frontend-app")

    assert generated
    content = (artifacts_dir / "current-state" / "module-dependency-map.md").read_text()
    assert "MODULE DEPENDENCY MAP" in content
    ui_api_content = (artifacts_dir / "current-state" / "ui-to-api-interactions.md").read_text()
    assert "ROUTE MAP" in ui_api_content
    assert "API CLIENT SUMMARY" in ui_api_content
    assert "API ENDPOINTS" in ui_api_content


def test_backfill_required_artifacts_fullstack_generates_frontend_interaction_views(tmp_path, monkeypatch):
    artifacts_dir = tmp_path / "artifacts"

    class FakeToolkit:
        def __init__(self, backend, repo_path=""):
            pass

        def call(self, name: str):
            if name == "get_module_dependency_map":
                return "=== MODULE DEPENDENCY MAP ==="
            if name == "get_route_map":
                return "InventoryPage -> ReserveModal"
            if name == "get_api_client_summary":
                return "inventoryClient.getItems -> GET /inventory"
            if name == "get_api_endpoints":
                return "GET /inventory\nPOST /inventory/reserve"
            raise AssertionError(name)

    monkeypatch.setattr("codedoc.stages.agent.KuzuBackend", lambda path: object())
    monkeypatch.setattr("codedoc.stages.agent.ReverseEngineerToolkit", FakeToolkit)

    generated = _backfill_required_artifacts("fake-db", "fake-repo", str(artifacts_dir), "fullstack-app")

    assert len(generated) == 2
    assert (artifacts_dir / "current-state" / "module-dependency-map.md").exists()
    assert (artifacts_dir / "current-state" / "ui-to-api-interactions.md").exists()
    assert "inventoryClient.getItems" in (artifacts_dir / "current-state" / "ui-to-api-interactions.md").read_text()


def test_missing_target_state_artifacts_uses_backend_contract(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "target-state").mkdir(parents=True)
    (artifacts_dir / "target-state" / "bounded-contexts.md").write_text("ok")

    missing = _missing_target_state_artifacts(str(artifacts_dir), "backend-service")

    assert missing == [
        "target-state/strangler-fig.md",
    ]


def test_build_architect_prompt_does_not_reference_manifest_writing(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    prompt = _build_architect_prompt(
        "orientation",
        str(artifacts_dir),
        "inventory-service",
        "backend-service",
    )

    assert "`manifests/artifacts.json` is generated by the pipeline. Do not write it yourself." in prompt
    assert "Do NOT stop before writing manifests/artifacts.json." not in prompt


def test_flows_prompt_limits_cypher_and_pivots_on_weak_routes():
    prompt = _build_analyst_system_prompt(
        "analyst/flows",
        "/tmp/repo-db",
        "inventory-service",
        "orientation",
        "fullstack-app",
        repo_metrics={"total_source_files": 79, "total_loc": 4198},
    )

    assert "If `get_route_map` reports no route-like frontend structures, pivot immediately" in prompt
    assert "Limit ad hoc `query` / `execute_cypher` use to at most one targeted fallback" in prompt


def test_recovery_prompt_gives_api_spec_more_budget(tmp_path, monkeypatch):
    calls = {}

    class FakeToolkit:
        def __init__(self, backend, repo_path=""):
            pass

    def fake_run_loop(**kwargs):
        calls["max_turns"] = kwargs["max_turns"]
        calls["user_request"] = kwargs["user_request"]
        return {"status": "done", "artifacts": [], "events": [], "tool_uses": 0, "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr("codedoc.stages.agent.KuzuBackend", lambda path: object())
    monkeypatch.setattr("codedoc.stages.agent.ReverseEngineerToolkit", FakeToolkit)
    monkeypatch.setattr("codedoc.stages.agent.run_loop", fake_run_loop)

    from codedoc.stages.agent import _recover_missing_current_state_artifacts

    _recover_missing_current_state_artifacts(
        provider=object(),
        kuzu_path="fake-db",
        repo_path="fake-repo",
        repo_name="inventory-service",
        artifacts_dir=str(tmp_path),
        primary_repo_type="backend-service",
        orientation_summary="orientation",
        repo_metrics=None,
        use_anthropic_format=False,
        max_context_tokens=120000,
        verbose=False,
        missing_paths=["current-state/api-spec.yaml"],
    )

    assert calls["max_turns"] == 24
    assert "OpenAPI 3.0 YAML grounded in observed HTTP endpoints" in calls["user_request"]


def test_build_architect_request_supports_fullstack():
    prompt = _build_architect_request("fullstack-app")

    assert "target-state/fullstack-boundaries.md" in prompt
    assert "target-state/migration-plan.md" in prompt


def test_write_executive_summary_is_professional_and_executive_facing(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    for rel_path, content in {
        "domain/business-capabilities.md": "# Business Capabilities\n\nSupports reservations, stock movement, and policy workflows.",
        "architecture/c4-context.md": "# C4 Context\n\nInteracts with admin frontend, database, and inventory APIs.",
        "current-state/module-dependency-map.md": "# Module Dependency Map\n\nFrontend and backend concerns still cross package boundaries.",
        "tech/coupling-hotspots.md": "# Coupling Hotspots\n\nInventory service and admin frontend are tightly coupled around movement flows.",
        "current-state/ui-to-api-interactions.md": "# UI to API Interactions\n\nAdministrative inventory screens depend on reservation and movement endpoints.",
        "target-state/fullstack-boundaries.md": "# Fullstack Boundaries\n\nSplit UI orchestration from inventory domain services.",
        "target-state/migration-plan.md": "# Migration Plan\n\nStage backend seams before frontend extraction.",
    }.items():
        full_path = artifacts_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)

    summary_path = _write_executive_summary(
        str(artifacts_dir),
        "inventory-service",
        "fullstack-app",
        ["http-api", "ui-routes", "persistence"],
        ["jvm", "js"],
        {
            "size_profile": "small",
            "artifacts": [
                {"path": "domain/business-capabilities.md", "class": "core"},
                {"path": "architecture/c4-context.md", "class": "core"},
                {"path": "current-state/module-dependency-map.md", "class": "core"},
                {"path": "tech/coupling-hotspots.md", "class": "core"},
                {"path": "target-state/fullstack-boundaries.md", "class": "target"},
                {"path": "target-state/migration-plan.md", "class": "target"},
            ],
        },
        [{"file": "architecture/user-journeys.md", "reason": "weak evidence"}],
    )

    content = Path(summary_path).read_text()
    assert "## Executive Overview" in content
    assert "## Current State" in content
    assert "## Key Risks" in content
    assert "## Recommendations" in content
    assert "## Confidence And Limitations" in content
    assert "## Artifact Index" not in content
    assert "Capabilities:" not in content
    assert "`fullstack-app` repository" not in content


def test_build_executive_summary_evidence_includes_metrics_and_omissions(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    for rel_path, content in {
        "domain/business-capabilities.md": "Supports inventory reservations.",
        "architecture/c4-context.md": "Used by admin tooling and upstream services.",
        "tech/coupling-hotspots.md": "Reservation and movement concerns are tightly coupled.",
        "target-state/migration-plan.md": "Sequence extraction around inventory seams.",
    }.items():
        full_path = artifacts_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)

    evidence = _build_executive_summary_evidence(
        str(artifacts_dir),
        "inventory-service",
        "backend-service",
        ["http-api", "persistence"],
        ["jvm"],
        {
            "artifacts": [
                {"path": "domain/business-capabilities.md", "class": "core"},
                {"path": "target-state/migration-plan.md", "class": "target"},
            ]
        },
        [{"file": "architecture/user-journeys.md", "reason": "weak evidence"}],
        {"total_loc": 1234, "total_source_files": 12, "size_band": "small", "risk_level": "medium"},
    )

    assert "Repo name: inventory-service" in evidence
    assert "LOC: 1,234" in evidence
    assert "architecture/user-journeys.md: weak evidence" in evidence
    assert "### domain/business-capabilities.md" in evidence


def test_run_executive_summary_phase_uses_dedicated_prompt_and_records_tokens(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "domain").mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "domain" / "business-capabilities.md").write_text("Supports reservations and movement workflows.")

    class FakeProvider:
        def chat(self, messages, tools=None, tool_choice="auto"):
            from codedoc.llm import LLMResponse

            assert tools is None
            assert "leadership" in messages[0]["content"].lower()
            assert "Executive Summary Evidence Pack" in messages[1]["content"]
            return LLMResponse(
                content="# Executive Summary\n\n## Executive Overview\n\nLeadership brief.\n\n## Current State Assessment\n\nStable.\n\n## Material Risks\n\n- Risk.\n\n## Strategic Recommendations\n\n- Act.\n\n## Execution Priorities\n\n- Now.\n\n## Confidence And Limitations\n\n- Moderate.\n",
                input_tokens=321,
                output_tokens=123,
            )

    result = _run_executive_summary_phase(
        FakeProvider(),
        str(artifacts_dir),
        "inventory-service",
        "backend-service",
        ["http-api"],
        ["jvm"],
        {"artifacts": [{"path": "domain/business-capabilities.md", "class": "core"}]},
        [],
        {"total_loc": 100, "total_source_files": 2, "size_band": "small", "risk_level": "low"},
    )

    content = Path(result["path"]).read_text()
    assert result["status"] == "done"
    assert result["input_tokens"] == 321
    assert result["output_tokens"] == 123
    assert "## Strategic Recommendations" in content


def test_write_c4_artifact_renders_deterministic_context_plantuml(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    result = write_c4_artifact(
        str(artifacts_dir),
        "architecture/c4-context.md",
        "System Context - Inventory Service",
        "Inventory Service is a backend system for managing stock.",
        json.dumps(
            {
                "people": [
                    {"id": "user", "name": "User", "description": "Primary actor"},
                ],
                "systems": [
                    {"id": "inventory_service", "name": "Inventory Service", "description": "Spring backend"},
                ],
                "external_systems": [
                    {"id": "postgres", "name": "PostgreSQL", "description": "Persistence", "kind": "database"},
                ],
                "relations": [
                    {"from": "user", "to": "inventory_service", "label": "Uses", "technology": "REST/HTTP"},
                    {"from": "inventory_service", "to": "postgres", "label": "Reads/Writes", "technology": "JDBC"},
                ],
            }
        ),
    )

    assert result.startswith("written:")
    content = (artifacts_dir / "architecture" / "c4-context.md").read_text()
    assert "```plantuml" in content
    assert '!include <C4/C4_Context>' in content
    assert 'Person(user, "User", "Primary actor")' in content
    assert 'SystemDb_Ext(postgres, "PostgreSQL", "Persistence")' in content


def test_write_c4_artifact_falls_back_to_name_for_missing_ids(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    result = write_c4_artifact(
        str(artifacts_dir),
        "architecture/c4-context.md",
        "Context",
        "Summary.",
        json.dumps(
            {
                "people": [
                    {"name": "Warehouse Operator", "description": "Primary actor"},
                ],
                "systems": [
                    {"name": "Inventory Service", "description": "Backend service"},
                ],
                "external_systems": [
                    {"name": "Relational Database", "description": "Persistence", "kind": "database"},
                ],
                "relations": [
                    {"from": "Warehouse Operator", "to": "Inventory Service", "label": "Uses", "technology": "REST/HTTP"},
                    {"from": "Inventory Service", "to": "Relational Database", "label": "Reads/Writes", "technology": "JDBC"},
                ],
            }
        ),
    )

    assert result.startswith("written:")
    content = (artifacts_dir / "architecture" / "c4-context.md").read_text()
    assert 'Person(Warehouse_Operator, "Warehouse Operator", "Primary actor")' in content
    assert 'System(Inventory_Service, "Inventory Service", "Backend service")' in content
    assert 'SystemDb_Ext(Relational_Database, "Relational Database", "Persistence")' in content
    assert 'Rel(Warehouse_Operator, Inventory_Service, "Uses", "REST/HTTP")' in content


def test_write_c4_artifact_rejects_missing_relation_endpoints(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    try:
        write_c4_artifact(
            str(artifacts_dir),
            "architecture/c4-context.md",
            "Broken Context",
            "Summary.",
            json.dumps(
                {
                    "people": [{"name": "Warehouse Operator", "description": "Actor"}],
                    "systems": [{"name": "Inventory Service", "description": "Backend"}],
                    "external_systems": [],
                    "relations": [{"label": "Uses"}],
                }
            ),
        )
    except ValueError as exc:
        assert "Relation 1 must include non-empty `from` and `to`." in str(exc)
    else:
        raise AssertionError("Expected invalid C4 relation spec to raise ValueError")
