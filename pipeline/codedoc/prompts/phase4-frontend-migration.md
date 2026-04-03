---
**Supervisor task override — Phase 4 (frontend) only.**

Ignore the general workflow above. Your **sole task** this run is to analyse component
complexity and modernisation opportunities, then write:
- `migration/roadmap.md`
- `target-state/blueprint.md`
- `manifests/artifacts.json`

Stop as soon as those artifacts are written.

---

## Phase 4 — Frontend Modernisation & Target Architecture

### Prior analysis context

The sections below contain summaries from Phase 2 (inventory) and Phase 3 (architecture)
which ran before this agent started. Use them — do not re-query tools already summarised.

### Tool strategy

**Batch in one turn:**
- `get_hotspots` — large/highly-coupled components (complexity hotspots)
- `get_unused_code` — unused components, hooks, utilities

**Then targeted queries:**

```cypher
-- Components with many imports (large, complex components)
MATCH (m:Module)-[:IMPORTS]->(dep)
WITH m.name AS module, count(dep) AS import_count
WHERE import_count > 8
RETURN module, import_count ORDER BY import_count DESC LIMIT 20
```

```cypher
-- Components imported by many others (high fan-in = hard to change)
MATCH (importer:Module)-[:IMPORTS]->(m:Module)
WITH m.name AS module, count(importer) AS used_by
WHERE used_by > 3
RETURN module, used_by ORDER BY used_by DESC LIMIT 20
```

### Write: `migration/roadmap.md`

Required sections:

- **## Component Complexity Hotspots [Observed]** — table:

  | Component | Issue | Imports | Used by | Risk |
  |-----------|-------|---------|---------|------|
  | `UserDashboard` | Too many concerns | 14 | 3 | High |

- **## Dead Code Candidates [Observed]** — unused components, hooks, or utilities (top-10 max)

- **## Modernisation Phases [Prescriptive]** — concrete, ordered phases. Examples:
  - Phase 1: Extract reusable UI primitives into a design system
  - Phase 2: Replace prop-drilling with targeted context/state management
  - Phase 3: Split god-components into container + presentational
  - Phase 4: Introduce code-splitting for heavy pages

  **NO calendar dates or quarters.** Phase order and relative priority only.

- **## Migration Risks [Inferred]** — specific risks in THIS codebase:
  e.g., circular imports, shared mutable state, tightly coupled components

Keep under 250 lines.

### Write: `target-state/blueprint.md`

Required sections:

- **## Target Component Architecture [Prescriptive]** — proposed structure:
  ```
  src/
  ├── features/          # one directory per business feature
  │   └── <feature>/
  │       ├── components/
  │       ├── hooks/
  │       └── api/
  ├── shared/            # design system, common hooks, utilities
  └── app/               # routing, global providers, layout
  ```
  Adapt to what the codebase actually needs — don't copy this verbatim.

- **## Modernisation Principles [Prescriptive]** — only principles relevant to THIS codebase:
  - Colocation (styles, tests, types near the component)
  - Single responsibility per component
  - Custom hooks for logic extraction
  - Lazy loading for route-level code splitting
  - etc. — be specific, not generic

- **## Open Questions [Unknown]** — gaps where the graph data was insufficient to recommend:
  e.g., "Could not determine if authentication state is centralised or spread across components"

Keep under 200 lines. Ground recommendations in the hotspot and coupling data.

### Write: `manifests/artifacts.json`

List **all artifacts written across all phases**:
```json
{
  "version": "1.0",
  "repo_name": "<repo>",
  "generated_at": "<ISO 8601>",
  "repo_type": "frontend",
  "artifacts": [
    {"file": "current-state/inventory.md", "phase": 2, "evidence": "Observed"},
    {"file": "architecture/system-overview.md", "phase": 3, "evidence": "Observed+Inferred"},
    {"file": "domain/domain-analysis.md", "phase": 3, "evidence": "Inferred"},
    {"file": "migration/roadmap.md", "phase": 4, "evidence": "Observed+Prescriptive"},
    {"file": "target-state/blueprint.md", "phase": 4, "evidence": "Prescriptive"}
  ]
}
```
