from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchetypeDefinition:
    key: str
    guidance_file: str
    analyst_required: tuple[str, ...]
    analyst_optional: tuple[str, ...]
    architect_required: tuple[str, ...]
    architect_sequence: tuple[str, ...]
    forbidden: tuple[str, ...]
    analyst_requests: dict[str, str]
    architect_request: str


_BACKEND_DOMAIN_TURN4 = (
    "TURN 4 — call write_artifact('domain/er-diagram.md', content) if persistent entities "
    "were found in Turns 1-2 (look for @Entity, repositories, ORM annotations). "
    "Content: Mermaid erDiagram + bounded context ownership table. "
    "If no persistent entities found, write a one-line file: "
    "'_No persistent entities found in graph._'\n"
)
_BACKEND_FLOWS_TURN2 = (
    "TURN 2 — call trace_user_flow on the 3 most important mutation entry points "
    "(POST/PUT/PATCH/DELETE) from Turn 1.\n"
)
_BACKEND_FLOWS_TURN3 = (
    "TURN 3 — call write_artifact('architecture/business-journeys.md', content). "
    "3-5 flows each with: '**Business journey:** As a [role], I can [action] by calling "
    "[METHOD /path].' followed by a Mermaid sequence diagram fenced as ```mermaid. "
    "Use sequenceDiagram syntax with participants and arrows.\n"
)
_BACKEND_TECH_TURN3 = (
    "TURN 3 — call write_artifact('tech/coupling-hotspots.md', content). "
    "Content: hotspot table (component | type | score | migration impact), "
    "top-5 coupling pairs, dead code top-10, decomposition signals "
    "(packages with low coupling = good extraction candidates).\n"
)


ARCHETYPES: dict[str, ArchetypeDefinition] = {
    "backend-service": ArchetypeDefinition(
        key="backend-service",
        guidance_file="archetype-backend-service.md",
        analyst_required=(
            "domain/business-capabilities.md",
            "architecture/business-journeys.md",
            "architecture/c4-context.md",
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
            "tech/coupling-hotspots.md",
        ),
        analyst_optional=(),
        architect_required=(
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
            "manifests/artifacts.json",
        ),
        architect_sequence=(
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
            "manifests/artifacts.json",
        ),
        forbidden=(),
        analyst_requests={
            "analyst/domain": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_schema, get_domain_model, get_annotations_usage.\n"
                "TURN 2 — batch in one response: get_class_details on the 5 most important entity classes "
                "found in Turn 1 (prefer @Entity annotated or aggregate root classes); "
                "execute_cypher: MATCH (n) WHERE n.name =~ "
                "'(?i).*(Event|Command|Created|Confirmed|Cancelled|Published|Topic).*' "
                "RETURN label(n) AS type, n.name AS name LIMIT 30.\n"
                "TURN 3 — call write_artifact('domain/business-capabilities.md', content). "
                "One section per capability: name in business terms, core operations (bullets), "
                "business rules/validations in business language with evidence citations, key entities.\n"
                + _BACKEND_DOMAIN_TURN4 +
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/flows": (
                "Execute in 5 turns.\n"
                "TURN 1 — batch in one response: get_entry_points, get_api_endpoints, "
                "get_external_dependencies, "
                "execute_cypher: MATCH (n) WHERE n.name =~ "
                "'(?i).*(Client|Producer|Consumer|Gateway|Adapter|Listener|Sender|Subscriber).*' "
                "RETURN label(n) AS type, n.name AS name LIMIT 40.\n"
                + _BACKEND_FLOWS_TURN2 +
                _BACKEND_FLOWS_TURN3 +
                "TURN 4 — call write_c4_artifact('architecture/c4-context.md', title, summary, spec_json). "
                "Use structured data only. spec_json must contain people, systems, external_systems, and relations arrays. "
                "external_systems kinds: system, database, queue. relations use from, to, label, technology, bidirectional.\n"
                "TURN 5 — call write_artifact('current-state/api-spec.yaml', content). "
                "Write an OpenAPI 3.0 YAML grounded in the observed HTTP endpoints. Keep schemas minimal when necessary, but do not skip the artifact for backend repos.\n"
                "Do NOT call get_method_source. Stop after Turn 5."
            ),
            "analyst/tech": (
                "Execute in 3 turns.\n"
                "TURN 1 — batch in one response: get_hotspots(coupling), get_hotspots(fan_in), "
                "get_hotspots(fan_out), get_hotspots(god_class), get_component_coupling_matrix, "
                "detect_circular_dependencies, get_unused_code, get_design_patterns.\n"
                "TURN 2 — call impact_analysis on the top 3 hotspot components from Turn 1.\n"
                + _BACKEND_TECH_TURN3 +
                "Do NOT call get_method_source. Stop after Turn 3."
            ),
        },
        architect_request=(
            "Write the 2 target-state artifacts in order:\n"
            "TURN 1: write_artifact('target-state/bounded-contexts.md', ...)\n"
            "TURN 2: write_artifact('target-state/strangler-fig.md', ...)\n"
            "Do NOT write manifests/artifacts.json; the pipeline will generate it.\n"
            "Do NOT call any graph query tools. Stop after Turn 2."
        ),
    ),
    "frontend-app": ArchetypeDefinition(
        key="frontend-app",
        guidance_file="archetype-frontend-app.md",
        analyst_required=(
            "architecture/route-map.md",
            "architecture/component-boundaries.md",
            "architecture/user-journeys.md",
            "current-state/ui-to-api-interactions.md",
            "current-state/state-management.md",
            "current-state/data-fetching-and-api-clients.md",
            "current-state/module-dependency-map.md",
            "tech/coupling-hotspots.md",
        ),
        analyst_optional=(),
        architect_required=(
            "target-state/frontend-boundaries.md",
            "target-state/migration-plan.md",
            "manifests/artifacts.json",
        ),
        architect_sequence=(
            "target-state/frontend-boundaries.md",
            "target-state/migration-plan.md",
            "manifests/artifacts.json",
        ),
        forbidden=(
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
        ),
        analyst_requests={
            "analyst/domain": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_schema, get_route_map, get_state_management_summary.\n"
                "TURN 2 — batch in one response: get_component_boundary_map, get_entry_points, get_external_dependencies.\n"
                "TURN 3 — call write_artifact('architecture/route-map.md', content). "
                "Content: route/screen table, owners, entry components, and notable guarded routes grounded in graph evidence.\n"
                "TURN 4 — call write_artifact('current-state/state-management.md', content). "
                "Content: stores, contexts, hooks, async state boundaries, cache/query layers, and ownership notes. "
                "Do NOT write 'likely state variables' lists or speculative shared-state claims unless directly evidenced.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/flows": (
                "Execute in 5 turns.\n"
                "TURN 1 — batch in one response: get_route_map, get_api_client_summary, get_external_dependencies, get_entry_points.\n"
                "TURN 2 — call trace_user_flow on up to 3 highest-signal user journeys from Turn 1. "
                "Prefer routes, screens, actions, or top-level components. If Turn 1 reports no route-like frontend structures, skip trace_user_flow and pivot to the strongest client-side/API evidence instead.\n"
                "TURN 3 — call write_artifact('architecture/user-journeys.md', content). "
                "Document 3-5 user journeys grounded in routes/components. If you include diagrams, fence them as ```mermaid blocks.\n"
                "TURN 4 — call write_artifact('current-state/ui-to-api-interactions.md', content). "
                "Content: which routes/components/hooks call which API clients and endpoints, plus notable coupling points. "
                "Do NOT invent auth headers/tokens, retries, polling services, external validation services, or initialization steps without direct evidence.\n"
                "TURN 5 — call write_artifact('architecture/component-boundaries.md', content). "
                "Content: top-level screens, shared layout, reusable UI, feature modules, and cross-feature coupling.\n"
                "Do NOT call get_method_source. Do NOT write backend-only artifacts. Avoid ad hoc execute_cypher unless a required artifact is blocked after the standard tools above. Stop after Turn 5."
            ),
            "analyst/tech": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_module_dependency_map, get_component_coupling_matrix, get_hotspots(coupling), "
                "detect_circular_dependencies, get_unused_code.\n"
                "TURN 2 — call impact_analysis on the top 3 modules/components from Turn 1.\n"
                "TURN 3 — call write_artifact('current-state/module-dependency-map.md', content). "
                "Content: feature/module dependency summary, shared utilities, cycles, and extraction seams.\n"
                "TURN 4 — call write_artifact('tech/coupling-hotspots.md', content). "
                "Content: hotspot table, cycles, dead code, and migration impact on modules/components.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
        },
        architect_request=(
            "Write the 2 target-state artifacts in order:\n"
            "TURN 1: write_artifact('target-state/frontend-boundaries.md', ...)\n"
            "TURN 2: write_artifact('target-state/migration-plan.md', ...)\n"
            "Do NOT write manifests/artifacts.json; the pipeline will generate it.\n"
            "Do NOT call any graph query tools. Do NOT write backend-only artifacts. Stop after Turn 2."
        ),
    ),
    "fullstack-app": ArchetypeDefinition(
        key="fullstack-app",
        guidance_file="archetype-fullstack-app.md",
        analyst_required=(
            "domain/business-capabilities.md",
            "architecture/c4-context.md",
            "architecture/route-map.md",
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
            "architecture/component-boundaries.md",
            "architecture/user-journeys.md",
            "current-state/ui-to-api-interactions.md",
            "current-state/state-management.md",
            "current-state/data-fetching-and-api-clients.md",
            "current-state/module-dependency-map.md",
            "tech/coupling-hotspots.md",
        ),
        analyst_optional=(),
        architect_required=(
            "target-state/fullstack-boundaries.md",
            "target-state/migration-plan.md",
            "manifests/artifacts.json",
        ),
        architect_sequence=(
            "target-state/fullstack-boundaries.md",
            "target-state/migration-plan.md",
            "manifests/artifacts.json",
        ),
        forbidden=(),
        analyst_requests={
            "analyst/domain": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_schema, get_domain_model, get_api_endpoints, get_annotations_usage.\n"
                "TURN 2 — batch in one response: get_class_details on the 5 most important domain/entity classes from Turn 1; "
                "execute_cypher: MATCH (n) WHERE n.name =~ "
                "'(?i).*(Event|Command|Created|Confirmed|Cancelled|Published|Topic).*' "
                "RETURN label(n) AS type, n.name AS name LIMIT 30.\n"
                "TURN 3 — call write_artifact('domain/business-capabilities.md', content). "
                "Content: business capabilities, backend ownership boundaries, key entities, and business rules grounded in evidence.\n"
                "TURN 4 — call write_artifact('domain/er-diagram.md', content). "
                "If persistent entities are found, write a Mermaid erDiagram with key entities and important relationships. "
                "If persistent entities are weakly evidenced, write a short evidence-based note rather than inventing relationships.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/flows": (
                "Execute in 5 turns.\n"
                "TURN 1 — batch in one response: get_route_map, get_api_endpoints, get_api_client_summary, get_external_dependencies, get_entry_points.\n"
                "TURN 2 — call trace_user_flow on up to 3 highest-signal end-to-end journeys from Turn 1. "
                "Prefer journeys that cross UI route, API, and persistence/integration boundaries. If Turn 1 reports no route-like frontend structures, skip trace_user_flow and pivot to the strongest API/client evidence already gathered.\n"
                "TURN 3 — call write_c4_artifact('architecture/c4-context.md', title, summary, spec_json). "
                "Use structured data only. spec_json must contain people, systems, external_systems, and relations arrays.\n"
                "TURN 4 — call write_artifact('architecture/user-journeys.md', content). "
                "Document 3-5 end-to-end journeys grounded in routes/components and backend entry points. If UI route evidence is weak, write integration journeys from API entry points and note the frontend gap explicitly.\n"
                "TURN 5 — call write_artifact('current-state/ui-to-api-interactions.md', content). "
                "Content: which routes/components/hooks call which API clients and backend endpoints, plus async boundaries and integration seams. "
                "For backend APIs present in the repo, current-state/api-spec.yaml is mandatory and may be completed in recovery if needed.\n"
                "Do NOT call get_method_source. Avoid ad hoc execute_cypher unless a required artifact is blocked after the standard tools above. Stop after Turn 5."
            ),
            "analyst/tech": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_module_dependency_map, get_component_coupling_matrix, get_hotspots(coupling), "
                "detect_circular_dependencies, get_unused_code.\n"
                "TURN 2 — call impact_analysis on the top 3 cross-boundary modules/components from Turn 1.\n"
                "TURN 3 — call write_artifact('current-state/state-management.md', content). "
                "Content: frontend state stores/hooks/contexts, data ownership, and where state crosses backend boundaries.\n"
                "TURN 4 — call write_artifact('current-state/module-dependency-map.md', content) and write_artifact('tech/coupling-hotspots.md', content) in the same response. "
                "First artifact: module/package dependency summary across frontend and backend. "
                "Second artifact: hotspot table, cycles, dead code, and extraction seams.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
        },
        architect_request=(
            "Write the 2 target-state artifacts in order:\n"
            "TURN 1: write_artifact('target-state/fullstack-boundaries.md', ...)\n"
            "TURN 2: write_artifact('target-state/migration-plan.md', ...)\n"
            "Do NOT write manifests/artifacts.json; the pipeline will generate it.\n"
            "Do NOT call any graph query tools. Keep frontend/backend concerns connected. Stop after Turn 2."
        ),
    ),
    "library": ArchetypeDefinition(
        key="library",
        guidance_file="archetype-library.md",
        analyst_required=(
            "architecture/public-surface.md",
            "current-state/core-abstractions.md",
            "current-state/extension-points.md",
            "current-state/module-structure.md",
            "current-state/dependency-map.md",
            "tech/coupling-hotspots.md",
        ),
        analyst_optional=(),
        architect_required=(
            "target-state/api-evolution.md",
            "target-state/refactoring-seams.md",
            "target-state/migration-guidance.md",
            "manifests/artifacts.json",
        ),
        architect_sequence=(
            "target-state/api-evolution.md",
            "target-state/refactoring-seams.md",
            "target-state/migration-guidance.md",
            "manifests/artifacts.json",
        ),
        forbidden=(
            "domain/er-diagram.md",
            "current-state/api-spec.yaml",
            "target-state/bounded-contexts.md",
            "target-state/strangler-fig.md",
        ),
        analyst_requests={
            "analyst/domain": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_schema, get_public_api_surface, get_extension_points.\n"
                "TURN 2 — batch in one response: get_design_patterns, get_architecture_overview, get_entry_points.\n"
                "TURN 3 — call write_artifact('architecture/public-surface.md', content). "
                "Content: exported/public APIs, important modules, expected consumers, and compatibility-sensitive areas.\n"
                "TURN 4 — call write_artifact('current-state/core-abstractions.md', content). "
                "Content: core abstractions, key types/interfaces, invariants, and responsibility boundaries.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/flows": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_public_api_surface, get_extension_points, get_external_dependencies, get_architecture_overview.\n"
                "TURN 2 — call trace_user_flow on the 3 most important public APIs or extension seams from Turn 1.\n"
                "TURN 3 — call write_artifact('current-state/extension-points.md', content). "
                "Content: plugin hooks, callbacks, interfaces, subclass points, configuration seams, and lifecycle expectations.\n"
                "TURN 4 — call write_artifact('current-state/module-structure.md', content). "
                "Content: package/module layout, responsibilities, and consumer usage flows rather than HTTP journeys.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
            "analyst/tech": (
                "Execute in 4 turns.\n"
                "TURN 1 — batch in one response: get_module_dependency_map, get_component_coupling_matrix, get_hotspots(coupling), "
                "detect_circular_dependencies, get_unused_code.\n"
                "TURN 2 — call impact_analysis on the top 3 modules/packages from Turn 1.\n"
                "TURN 3 — call write_artifact('current-state/dependency-map.md', content). "
                "Content: internal package dependencies, external dependencies, layering problems, and refactoring seams.\n"
                "TURN 4 — call write_artifact('tech/coupling-hotspots.md', content). "
                "Content: hotspot table, cycles, dead code, and API stability risks.\n"
                "Do NOT call get_method_source. Stop after Turn 4."
            ),
        },
        architect_request=(
            "Write the 3 target-state artifacts in order:\n"
            "TURN 1: write_artifact('target-state/api-evolution.md', ...)\n"
            "TURN 2: write_artifact('target-state/refactoring-seams.md', ...)\n"
            "TURN 3: write_artifact('target-state/migration-guidance.md', ...)\n"
            "Do NOT write manifests/artifacts.json; the pipeline will generate it.\n"
            "Do NOT call any graph query tools. Do NOT write backend-service migration plans. Stop after Turn 3."
        ),
    ),
}


def archetype_definition(archetype: str) -> ArchetypeDefinition:
    return ARCHETYPES.get(archetype, ARCHETYPES["backend-service"])


def resolve_archetype(signal_counts: dict[str, int], detected_language_categories: list[str]) -> str:
    if signal_counts.get("frontend-ui", 0) > 0 and signal_counts.get("backend-api", 0) > 0:
        return "fullstack-app"
    if signal_counts.get("frontend-ui", 0) > 0:
        return "frontend-app"
    if signal_counts.get("backend-api", 0) > 0:
        return "backend-service"
    if signal_counts.get("library", 0) > 0:
        return "library"
    if detected_language_categories == ["js"]:
        return "library"
    return "backend-service"
