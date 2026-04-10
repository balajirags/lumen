from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codedoc.archetype_registry import archetype_definition


@dataclass(frozen=True)
class ArtifactDefinition:
    path: str
    artifact_class: str  # core | conditional | target | manifest
    summary_profile: str  # compact | standard
    purpose: str
    required_sections: tuple[str, ...] = ()
    backfillable: bool = False


@dataclass(frozen=True)
class ArtifactPackDefinition:
    key: str
    artifacts: tuple[str, ...]


ARTIFACT_DEFINITIONS: dict[str, ArtifactDefinition] = {
    "summary/executive-summary.md": ArtifactDefinition(
        path="summary/executive-summary.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Leadership-facing synthesis of repo purpose, operating posture, material risks, and recommended next actions.",
        required_sections=(
            "Executive Overview",
            "Current State Assessment",
            "Material Risks",
            "Strategic Recommendations",
            "Execution Priorities",
            "Confidence And Limitations",
        ),
    ),
    "domain/business-capabilities.md": ArtifactDefinition(
        path="domain/business-capabilities.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Observed backend or domain capability summary.",
    ),
    "architecture/business-journeys.md": ArtifactDefinition(
        path="architecture/business-journeys.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Observed service workflows and integration journeys.",
    ),
    "architecture/c4-context.md": ArtifactDefinition(
        path="architecture/c4-context.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="System context and external dependencies.",
    ),
    "current-state/api-surface.md": ArtifactDefinition(
        path="current-state/api-surface.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Observed HTTP/API surface when the repo exposes API entry points.",
        backfillable=True,
    ),
    "domain/data-model-summary.md": ArtifactDefinition(
        path="domain/data-model-summary.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Observed persistence model summary and important entities.",
        backfillable=True,
    ),
    "architecture/route-map.md": ArtifactDefinition(
        path="architecture/route-map.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Frontend or end-user route inventory at feature level.",
    ),
    "architecture/component-boundaries.md": ArtifactDefinition(
        path="architecture/component-boundaries.md",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="Frontend component/module boundaries.",
        backfillable=True,
    ),
    "architecture/user-journeys.md": ArtifactDefinition(
        path="architecture/user-journeys.md",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="Representative end-to-end flows.",
    ),
    "current-state/state-management.md": ArtifactDefinition(
        path="current-state/state-management.md",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="State ownership and boundaries for frontend-heavy repos.",
        backfillable=True,
    ),
    "current-state/data-fetching-and-api-clients.md": ArtifactDefinition(
        path="current-state/data-fetching-and-api-clients.md",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="Observed UI-to-API and client integration boundaries.",
    ),
    "current-state/ui-to-api-interactions.md": ArtifactDefinition(
        path="current-state/ui-to-api-interactions.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Observed mapping between UI routes/components, client modules, and backend API endpoints.",
        backfillable=True,
    ),
    "current-state/module-dependency-map.md": ArtifactDefinition(
        path="current-state/module-dependency-map.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Module/package dependency summary and seams.",
        backfillable=True,
    ),
    "tech/coupling-hotspots.md": ArtifactDefinition(
        path="tech/coupling-hotspots.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Top coupling hotspots and refactoring seams.",
    ),
    "domain/er-diagram.md": ArtifactDefinition(
        path="domain/er-diagram.md",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="Entity relationship model when strong persistence evidence exists.",
    ),
    "current-state/api-spec.yaml": ArtifactDefinition(
        path="current-state/api-spec.yaml",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="Observed API surface when endpoint evidence is strong.",
    ),
    "architecture/public-surface.md": ArtifactDefinition(
        path="architecture/public-surface.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Public API surface for libraries or CLIs.",
    ),
    "current-state/core-abstractions.md": ArtifactDefinition(
        path="current-state/core-abstractions.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="Core abstractions and invariants for libraries.",
    ),
    "current-state/extension-points.md": ArtifactDefinition(
        path="current-state/extension-points.md",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="Extension/plugin seams when strongly evidenced.",
    ),
    "current-state/module-structure.md": ArtifactDefinition(
        path="current-state/module-structure.md",
        artifact_class="core",
        summary_profile="standard",
        purpose="High-level module or package structure.",
    ),
    "current-state/dependency-map.md": ArtifactDefinition(
        path="current-state/dependency-map.md",
        artifact_class="conditional",
        summary_profile="standard",
        purpose="Detailed internal/external dependency layout.",
    ),
    "target-state/bounded-contexts.md": ArtifactDefinition(
        path="target-state/bounded-contexts.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="Target decomposition for backend services.",
    ),
    "target-state/strangler-fig.md": ArtifactDefinition(
        path="target-state/strangler-fig.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="Incremental migration plan for backend services.",
    ),
    "target-state/frontend-boundaries.md": ArtifactDefinition(
        path="target-state/frontend-boundaries.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="Target UI/domain boundary recommendations.",
    ),
    "target-state/fullstack-boundaries.md": ArtifactDefinition(
        path="target-state/fullstack-boundaries.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="Target frontend/backend seam recommendations.",
    ),
    "target-state/migration-plan.md": ArtifactDefinition(
        path="target-state/migration-plan.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="Incremental migration and implementation sequence.",
    ),
    "target-state/api-evolution.md": ArtifactDefinition(
        path="target-state/api-evolution.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="API evolution guidance for libraries.",
    ),
    "target-state/refactoring-seams.md": ArtifactDefinition(
        path="target-state/refactoring-seams.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="Library refactoring seams and compatibility boundaries.",
    ),
    "target-state/migration-guidance.md": ArtifactDefinition(
        path="target-state/migration-guidance.md",
        artifact_class="target",
        summary_profile="standard",
        purpose="Migration guidance for consumers of the library.",
    ),
    "manifests/artifacts.json": ArtifactDefinition(
        path="manifests/artifacts.json",
        artifact_class="manifest",
        summary_profile="compact",
        purpose="Machine-readable artifact index and omission metadata.",
    ),
}


PACKS: dict[str, ArtifactPackDefinition] = {
    "executive": ArtifactPackDefinition("executive", ("summary/executive-summary.md",)),
    "backend-core": ArtifactPackDefinition(
        "backend-core",
        (
            "domain/business-capabilities.md",
            "architecture/business-journeys.md",
            "architecture/c4-context.md",
            "tech/coupling-hotspots.md",
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
        ),
    ),
    "frontend-core": ArtifactPackDefinition(
        "frontend-core",
        (
            "architecture/user-journeys.md",
            "current-state/ui-to-api-interactions.md",
            "current-state/state-management.md",
            "current-state/data-fetching-and-api-clients.md",
            "current-state/module-dependency-map.md",
            "tech/coupling-hotspots.md",
        ),
    ),
    "fullstack-core": ArtifactPackDefinition(
        "fullstack-core",
        (
            "domain/business-capabilities.md",
            "architecture/c4-context.md",
            "architecture/user-journeys.md",
            "current-state/ui-to-api-interactions.md",
            "current-state/state-management.md",
            "current-state/data-fetching-and-api-clients.md",
            "current-state/module-dependency-map.md",
            "tech/coupling-hotspots.md",
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
        ),
    ),
    "library-core": ArtifactPackDefinition(
        "library-core",
        (
            "architecture/public-surface.md",
            "current-state/core-abstractions.md",
            "current-state/extension-points.md",
            "current-state/module-structure.md",
            "current-state/dependency-map.md",
            "tech/coupling-hotspots.md",
        ),
    ),
    "backend-target": ArtifactPackDefinition(
        "backend-target",
        ("target-state/bounded-contexts.md", "target-state/strangler-fig.md", "manifests/artifacts.json"),
    ),
    "frontend-target": ArtifactPackDefinition(
        "frontend-target",
        ("target-state/frontend-boundaries.md", "target-state/migration-plan.md", "manifests/artifacts.json"),
    ),
    "fullstack-target": ArtifactPackDefinition(
        "fullstack-target",
        ("target-state/fullstack-boundaries.md", "target-state/migration-plan.md", "manifests/artifacts.json"),
    ),
    "library-target": ArtifactPackDefinition(
        "library-target",
        ("target-state/api-evolution.md", "target-state/refactoring-seams.md", "target-state/migration-guidance.md", "manifests/artifacts.json"),
    ),
}


REPO_TYPE_PACKS: dict[str, tuple[str, ...]] = {
    "backend-service": ("executive", "backend-core", "backend-target"),
    "frontend-app": ("executive", "frontend-core", "frontend-target"),
    "fullstack-app": ("executive", "fullstack-core", "fullstack-target"),
    "library": ("executive", "library-core", "library-target"),
}


def _size_profile(repo_metrics: dict[str, Any] | None) -> str:
    size_band = str((repo_metrics or {}).get("size_band", "small"))
    if size_band in {"large", "xlarge"}:
        return "large"
    if size_band == "medium":
        return "medium"
    return "small"


def infer_capabilities(primary_repo_type: str, repo_metrics: dict[str, Any] | None) -> list[str]:
    metrics = repo_metrics or {}
    categories = set(metrics.get("detected_language_categories", []))
    signals = set(metrics.get("archetype_signals", []))
    signal_counts = dict(metrics.get("archetype_signal_counts", {}))
    capabilities: list[str] = []
    js_only = categories == {"js"}
    strong_backend_signal = signal_counts.get("backend-api", 0) >= (2 if js_only else 1)

    if "jvm" in categories or "python" in categories:
        capabilities.append("backend-runtime")
    if "js" in categories:
        capabilities.append("js-runtime")
    if "frontend-ui" in signals:
        capabilities.append("ui-routes")
    if strong_backend_signal:
        capabilities.append("http-api")
    if primary_repo_type in {"backend-service", "fullstack-app"}:
        capabilities.append("persistence")
    if primary_repo_type == "library":
        capabilities.append("public-api")
    return sorted(dict.fromkeys(capabilities))


def build_artifact_plan(primary_repo_type: str, repo_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    repo_type = primary_repo_type if primary_repo_type in REPO_TYPE_PACKS else "backend-service"
    size_profile = _size_profile(repo_metrics)
    capabilities = infer_capabilities(repo_type, repo_metrics)
    pack_keys = REPO_TYPE_PACKS[repo_type]
    signal_counts = dict((repo_metrics or {}).get("archetype_signal_counts", {}))

    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack_key in pack_keys:
        pack = PACKS[pack_key]
        for path in pack.artifacts:
            if path in seen:
                continue
            seen.add(path)

            definition = ARTIFACT_DEFINITIONS[path]
            artifact_class = definition.artifact_class
            if repo_type in {"backend-service", "fullstack-app"} and path in {"current-state/api-spec.yaml", "domain/er-diagram.md"}:
                # api-spec.yaml requires annotated routes or a resolved call graph to produce
                # valid YAML. Large/xlarge repos with sparse call graphs (JS without @GetMapping,
                # Kotlin without Spring annotations) fail to produce it reliably — keeping it
                # conditional for those bands means a missing spec no longer marks the run failed.
                if path == "current-state/api-spec.yaml" and size_profile == "large":
                    pass  # stays conditional — won't block run status
                else:
                    artifact_class = "core"
            if size_profile == "large" and artifact_class == "conditional":
                required = False
            else:
                required = artifact_class in {"core", "target", "manifest"}
            artifacts.append(
                {
                    "path": path,
                    "class": artifact_class,
                    "required": required,
                    "backfillable": definition.backfillable,
                    "summary_profile": definition.summary_profile,
                    "purpose": definition.purpose,
                    "required_sections": list(definition.required_sections),
                    "status": "planned",
                }
            )

    return {
        "primary_repo_type": repo_type,
        "capabilities": capabilities,
        "size_profile": size_profile,
        "packs": list(pack_keys),
        "artifacts": artifacts,
    }


def planned_artifacts_by_class(plan: dict[str, Any], artifact_class: str, *, required_only: bool = False) -> list[str]:
    paths: list[str] = []
    for item in plan.get("artifacts", []):
        if item.get("class") != artifact_class:
            continue
        if required_only and not item.get("required", False):
            continue
        paths.append(str(item["path"]))
    return paths


def classify_artifact_path(plan: dict[str, Any], rel_path: str) -> dict[str, Any] | None:
    for item in plan.get("artifacts", []):
        if item.get("path") == rel_path:
            return item
    return None


def artifact_status_snapshot(artifacts_dir: str, plan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = Path(artifacts_dir)
    written: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    for item in plan.get("artifacts", []):
        rel_path = str(item["path"])
        exists = (root / rel_path).exists()
        if exists:
            written.append(
                {
                    "file": rel_path,
                    "class": item["class"],
                    "status": "written",
                    "profile": item["summary_profile"],
                }
            )
        elif item["class"] == "conditional":
            omitted.append(
                {
                    "file": rel_path,
                    "reason": "Conditional artifact omitted because evidence was weak or generation was incomplete.",
                }
            )
    return written, omitted
