---
**Supervisor task override — Phase 3 only.**

Ignore the general workflow above. Your **sole task** this run is to analyse architecture
patterns and domain model, then write **two artifacts**:
- `architecture/system-overview.md`
- `domain/domain-analysis.md`

Stop as soon as both artifacts are written. Do NOT write any other artifacts.

---

## Phase 3 — Architecture & Domain Analysis

### Orientation context

The orientation summary below was gathered before this agent started. Use it to understand
the dominant language, framework, and graph size — do not repeat `get_architecture_summary`.

### Tool calls (batch all in a single turn)

1. **Batch together:** `get_design_patterns`, `get_component_coupling_matrix`, `get_domain_model`, `get_external_dependencies`

If the graph is large (> 1000 nodes), also call `get_class_hierarchy` on 2–3 key base classes.

### Write: `architecture/system-overview.md`

Open every major section with its evidence tag.

Required sections:
- **## Layered Architecture [Observed]** — layers, responsibilities, boundaries (text diagram or bullets)
- **## Top Hotspot Components [Observed]** — top 5 by coupling: name, score, brief risk note
- **## Data Flow [Inferred]** — request path → service → repository → external systems
- **## Design Patterns [Observed]** — patterns found with one example each (MVC, Observer, Repository, etc.)
- **## External Systems [Observed]** — databases, queues, caches, third-party APIs

Add a single cross-reference line: `_(Tech stack: see current-state/inventory.md)_`

Keep under 200 lines.

### Write: `domain/domain-analysis.md`

Required sections:
- **## Business Capabilities [Inferred]** — one short paragraph per capability: name, core operations, key invariants
- **## Bounded Context Candidates [Inferred]** — table: context name | aggregate root | upstream deps | downstream deps
- **## Domain Events [Observed]** — event topics and their consumers (if visible in graph)
- **## Capability Maturity [Hypothesized]** — one line per capability: Established / Developing / Gap, and why

Keep under 200 lines. Prefer bullets and tables. Do not repeat entity details — cross-reference: `_(Entities: see current-state/inventory.md)_`
