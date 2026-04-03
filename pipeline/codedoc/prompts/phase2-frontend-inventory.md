---
**Supervisor task override — Phase 2 (frontend) only.**

Ignore the general workflow above. Your **sole task** this run is to analyse the
component structure, routing, and state management, then write **one artifact**:
`current-state/inventory.md`.

Stop as soon as that artifact is written. Do NOT write any other artifacts.

---

## Phase 2 — Frontend Component & Module Inventory

### Orientation context

The orientation summary below was gathered before this agent started. Use it to understand
the dominant framework, graph size, and top-level module structure.
Do NOT repeat `get_architecture_summary`.

### Tool strategy

**Step 1 — batch in one turn:**
- `get_entry_points` — finds app entry, page components, route definitions
- `get_external_dependencies` — identifies framework, libraries, state management tools
- `get_hotspots` (default) — finds heavily-imported or large components

**Step 2 — module deep-dives:**
For each top-level feature directory visible in the orientation summary,
call `get_module_deep_dive(name)`.

**Step 3 — targeted Cypher for patterns:**

```cypher
-- All exported components/hooks with their module
MATCH (m:Module)-[:CONTAINS]->(c)
WHERE c.exported = true
RETURN m.name AS module, c.name AS name, label(c) AS type
ORDER BY module, name LIMIT 100
```

```cypher
-- State management library usage
MATCH (m:Module)-[:IMPORTS]->(dep)
WHERE dep.name =~ '(?i).*(redux|zustand|jotai|recoil|mobx|context|store|slice|reducer|atom).*'
RETURN m.name AS module, collect(dep.name) AS deps LIMIT 40
```

```cypher
-- API/data-fetching patterns
MATCH (m:Module)-[:IMPORTS]->(dep)
WHERE dep.name =~ '(?i).*(axios|fetch|http|api|client|swr|react-query|tanstack|graphql|urql).*'
RETURN m.name AS module, collect(dep.name) AS deps LIMIT 40
```

Do NOT call `get_api_endpoints`, `get_scheduled_jobs`, or `get_annotations_usage` — these
are backend-only tools that return nothing for frontend repos.

### Write: `current-state/inventory.md`

Open every major section with its evidence tag.

Required sections:

- **## Component Inventory [Observed]** — table:

  | Component | Type | Module path | Key imports / role |
  |-----------|------|-------------|-------------------|
  | `App` | entry | `src/App.tsx` | Router, global providers |
  | `Dashboard` | page | `src/pages/Dashboard.tsx` | fetches summary data |
  | ... | | | |

  Types: `entry`, `page`, `layout`, `feature`, `ui` (reusable primitive).

- **## Routing Structure [Observed]** — list of routes: path → component, note lazy-loaded routes

- **## State Management [Observed]** — what library/pattern is used; list stores/slices/contexts
  and which components they serve

- **## API Integration Layer [Observed]** — how the frontend fetches data:
  - HTTP client used (axios, fetch, react-query, etc.)
  - Known API base URLs or service files
  - Data-fetching patterns (hooks, services, effects)

- **## Technology Stack [Observed]** — framework + version, build tool, CSS approach, key
  libraries — **listed HERE ONLY; other artifacts must not repeat this**

Keep the artifact under 250 lines. Prefer bullets and tables over prose.
If a section has no findings, write `_No findings._` — never pad.
