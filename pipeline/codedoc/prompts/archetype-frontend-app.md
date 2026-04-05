Treat the repository as a frontend application.

- Prefer routes, screens, components, hooks, state boundaries, and API clients over backend-only concepts.
- Frame business journeys as user interaction flows.
- Required current-state outputs are route map, component boundaries, user journeys, state-management summary, data-fetching/API-client summary, and module dependency map.
- Required target-state outputs are frontend boundaries, a future-state diagram, and a migration plan.
- Forbidden outputs for this archetype include ER diagrams, OpenAPI specs, bounded-context decomposition, and strangler-fig plans.
- Do not force backend-only language such as aggregate roots, ER models, service extraction, Kafka, PostgreSQL, or ACL seams unless the graph clearly supports them.
- For current-state artifacts, do not invent auth flows, retries, polling services, external validation services, route libraries, initialization steps, or API gateway details unless the graph explicitly supports them.
- When evidence is weak, say so directly with `[Inferred]` or `[Unknown]` instead of upgrading the claim to `[Observed]`.
- When recommending target state, focus on component/module boundaries, shared state isolation, route ownership, and integration seams.
- Keep PlantUML diagrams fenced as ` ```plantuml ` blocks.
