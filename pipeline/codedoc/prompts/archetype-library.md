Treat the repository as a library or shared package.

- Prefer public API surface, package boundaries, extension points, and dependency seams.
- Frame flows as consumer usage flows rather than end-user journeys.
- Required current-state outputs are public surface, core abstractions, extension points, module structure, dependency map, and coupling hotspots.
- Required target-state outputs are API evolution guidance, refactoring seams, and migration guidance.
- Do not assume HTTP endpoints, persistence, service decomposition, ER models, or strangler-fig plans unless the graph clearly supports them.
- When recommending target state, focus on modularization, API stability, compatibility boundaries, and extraction of cohesive packages.
- Use the C4 diagram tool only when a C4 context diagram is required; prefer Mermaid for other diagrams.
