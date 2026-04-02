---
**Supervisor task override — Phase 4 only.**

Ignore the general workflow above. Your **sole task** this run is to analyse migration risk and
target state, then write:
- `migration/roadmap.md`
- `target-state/blueprint.md`
- `target-state/openapi/<ctx>.yaml` per bounded context (OPTIONAL — see conditions below)
- `manifests/artifacts.json`

Stop as soon as those artifacts are written.

---

## Phase 4 — Migration & Target State

### Prior analysis context

The sections below contain summaries from Phase 2 (inventory) and Phase 3 (architecture + domain)
which ran in parallel before this agent started. Use them — do not re-query tools that have
already been summarised here.

### Tool calls

1. **Batch together:** `get_hotspots` (run for types: coupling, fan_in, fan_out, god_class separately OR use the default which returns all)
2. `get_unused_code`
3. `impact_analysis` on the top 3 components from the hotspot results

Use `get_method_source` (budget: 15 calls) only for methods where you need implementation
details to accurately assess migration risk — e.g., a transaction boundary, a security check,
or a state machine that spans services.

### Write: `migration/roadmap.md`

Required sections:
- **## Risk Matrix [Observed]** — table: component | risk type | coupling score | estimated migration impact
- **## Dead Code Candidates [Observed]** — top-10 unused classes/methods (name + package)
- **## Modernization Phases [Prescriptive]** — Phase 1 → Phase N, each with: goal, key changes, key risks
  - **NO calendar dates or quarters** — phase ordering and relative sequencing only
- **## Migration Anti-Patterns [Inferred]** — specific patterns in THIS codebase that will complicate migration

Keep under 250 lines.

### Write: `target-state/blueprint.md`

Required sections:
- **## Target Service Map [Prescriptive]** — table: service name | responsibility | data owned | events published | events consumed
- **## Migration Principles [Prescriptive]** — bullets: strangler fig, database-per-service, etc.
  Only include principles that are relevant to THIS codebase's structure. No generic boilerplate.
- **## Open Questions [Unknown]** — gaps where graph analysis was insufficient to recommend

Keep under 200 lines. Ground every recommendation in the hotspot and coupling data — no generic CNCF stack advice.

### Write: `target-state/openapi/<ctx>.yaml` (OPTIONAL)

Write one YAML file per bounded context from `domain/domain-analysis.md` **only if** the graph
provides sufficient path annotations and method signatures to produce non-trivial schemas.
If the graph lacks this detail, add a one-line note in `blueprint.md` and skip the YAML files.

### Write: `manifests/artifacts.json`

List **all artifacts written across all phases** (not just this phase):
```json
{
  "version": "1.0",
  "repo_name": "<repo>",
  "generated_at": "<ISO 8601>",
  "artifacts": [
    {"file": "current-state/inventory.md", "phase": 2, "evidence": "Observed"},
    {"file": "architecture/system-overview.md", "phase": 3, "evidence": "Observed+Inferred"},
    {"file": "domain/domain-analysis.md", "phase": 3, "evidence": "Inferred"},
    {"file": "migration/roadmap.md", "phase": 4, "evidence": "Observed+Prescriptive"},
    {"file": "target-state/blueprint.md", "phase": 4, "evidence": "Prescriptive"}
  ]
}
```
