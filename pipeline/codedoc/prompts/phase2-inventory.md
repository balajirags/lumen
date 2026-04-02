---
**Supervisor task override — Phase 2 only.**

Ignore the general workflow above. Your **sole task** this run is to analyse the API surface
and module structure, then write **one artifact**: `current-state/inventory.md`.

Stop as soon as that artifact is written. Do NOT write any other artifacts.

---

## Phase 2 — API & Module Inventory

### Orientation context

The orientation summary below was gathered before this agent started. Use it to understand
the dominant language, framework, and graph size — do not repeat `get_architecture_summary`.

### Tool calls (batch independent ones in a single turn)

1. **Batch together:** `get_entry_points`, `get_api_endpoints`, `get_scheduled_jobs`, `get_annotations_usage`
2. For each top-level package visible in the orientation summary: `get_module_deep_dive(name)`
3. `get_class_hierarchy` on the 2–3 most important domain classes
4. `detect_circular_dependencies`

### Write: `current-state/inventory.md`

Open every major section with its evidence tag.

Required sections:
- **## API Surface [Observed]** — endpoints grouped by controller: path, HTTP method, one-line description
- **## Module Structure [Observed]** — table: package → class count → purpose (one row per package)
- **## Domain Entities [Observed]** — entity name + 3–5 key fields each
- **## Technology Stack [Observed]** — framework, persistence, cache, messaging — **listed HERE ONLY; other artifacts must not repeat this**
- **## Cross-Cutting Concerns [Observed]** — filters, interceptors, exception handlers, middleware

Keep the artifact under 250 lines. Prefer bullets and tables over prose paragraphs.
If a section has no findings, write `_No findings._` — never pad.
