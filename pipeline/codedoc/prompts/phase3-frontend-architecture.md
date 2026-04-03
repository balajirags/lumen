---
**Supervisor task override — Phase 3 (frontend) only.**

Ignore the general workflow above. Your **sole task** this run is to analyse component
hierarchy and feature organisation, then write **two artifacts**:
- `architecture/system-overview.md`
- `domain/domain-analysis.md`

Stop as soon as both artifacts are written. Do NOT write any other artifacts.

---

## Phase 3 — Frontend Architecture & Feature Analysis

### Orientation context

The orientation summary and Phase 2 inventory are injected below.
Do NOT repeat `get_architecture_summary`.

### Tool strategy

**Batch in one turn:**
- `get_component_coupling_matrix` — which modules depend on which (reveals architectural layers)
- `get_entry_points` — page/route components (top of component tree)
- `detect_circular_dependencies` — circular imports between modules

**Then targeted Cypher:**

```cypher
-- Import graph: which modules import which (for hierarchy reconstruction)
MATCH (a:Module)-[:IMPORTS]->(b:Module)
RETURN a.name AS importer, b.name AS imported
ORDER BY importer LIMIT 150
```

```cypher
-- Find custom hooks (React pattern)
MATCH (m:Module)-[:CONTAINS]->(f)
WHERE f.name =~ '^use[A-Z].*' AND label(f) IN ['Function', 'ArrowFunction']
RETURN m.name AS module, f.name AS hook
ORDER BY module, hook LIMIT 60
```

```cypher
-- Find HOC or wrapper patterns
MATCH (m:Module)-[:CONTAINS]->(f)
WHERE f.name =~ '^(with|With)[A-Z].*'
RETURN m.name AS module, f.name AS hoc LIMIT 30
```

```cypher
-- Shared/common components (imported by many)
MATCH (a:Module)-[:IMPORTS]->(b:Module)
WITH b.name AS shared, count(a) AS importers
WHERE importers > 2
RETURN shared, importers ORDER BY importers DESC LIMIT 20
```

### Write: `architecture/system-overview.md`

Open every major section with its evidence tag.

Required sections:

- **## Component Hierarchy [Observed]** — text tree or indented list showing the main layers:
  ```
  App (entry)
  ├── Layout / Shell
  │   ├── Header, Sidebar, Footer
  │   └── Router
  ├── Pages (one per route)
  │   ├── Feature components
  │   └── ...
  └── Shared / UI primitives
  ```

- **## Data Flow [Observed]** — how data moves through the app:
  API call → cache/store → container component → presentational component → user

- **## UI Patterns [Observed]** — patterns found:
  - Custom hooks (list with purpose)
  - HOC / wrapper patterns
  - Context providers
  - Compound components or render-prop patterns

- **## Code Organisation [Inferred]** — is it feature-based, layer-based, or mixed?
  What is the dominant folder convention?

- **## External Integrations [Observed]** — backend APIs, auth providers, analytics, CDNs,
  third-party SDKs the frontend calls or embeds

Add: `_(Tech stack: see current-state/inventory.md)_`
Keep under 200 lines.

### Write: `domain/domain-analysis.md`

Required sections:

- **## Feature Areas [Inferred]** — business features visible in the codebase:
  table: Feature | Key components | User actions it supports

- **## Key User Flows [Inferred]** — 3–5 most important end-to-end flows, e.g.:
  - "User logs in → JWT stored → Dashboard rendered with user data"
  - "User selects item → detail page loads → quantity updated → cart state modified"

- **## Shared UI Contracts [Observed]** — reusable components/hooks that multiple features
  depend on; their props or return values (from the graph, not source)

- **## Feature Maturity [Hypothesized]** — one line per feature area:
  Established / Developing / Incomplete, and reasoning

Add: `_(Tech stack: see current-state/inventory.md)_`
Keep under 200 lines.
