# Phase 2: Structural Improvements for xlarge Codebase Support

## Background

The current pipeline works well for small–large repos (up to ~200k LOC). At xlarge scale
three consecutive runs all failed to produce target-state artifacts:

| Repo | LOC | Time | Prune events | Target-state |
|---|---|---|---|---|
| openmrs-core | 230,710 | 97 min | 22 | ❌ 0 artifacts |
| mercur | 298,632 | 56 min | 9 | ❌ failed validation |
| medusa | 1,009,380 | 64 min | 20 | ❌ missing |

Structural artifacts (ER diagram, coupling hotspots, C4 context, business capabilities)
were consistently strong — openmrs produced some of the highest-quality individual artifacts
in the comparison. The failure point is target-state synthesis: writing `bounded-contexts.md`
and `strangler-fig.md` for a 230k LOC monolith requires synthesising evidence from the full
Phase 2 investigation, and the architect receives a truncated view after heavy pruning.

### Why xlarge is structurally different

The pipeline runs 3 parallel analysts each with a 120k token context window. On a small
repo (4k LOC), tool responses average ~6k tokens/call and the analyst completes with 0–1
prune events. On an xlarge repo, tool responses average 13–17k tokens/call because the
graph is dense — every `get_callers` or `get_class_details` call returns more results. The
domain analyst on openmrs alone consumed 628k input tokens across 22 prune events before
finishing. By the time the architect phase started, the available context was a truncated
summary of already-truncated analyst output.

The root tension: **the number of meaningful facts to convey scales with repo size, but
the context window does not.**

---

## Approach A — Domain Sharding (Recommended first step)

### Concept

Instead of 3 analysts each working across the entire repo (domain / flows / tech), split
Phase 2 into two sub-phases:

**Sub-phase 2a:** A single coordinator queries `get_domains` and assigns 2–3 domains to
each of N analyst instances. Each analyst investigates only its assigned domains.

**Sub-phase 2b:** A synthesis pass collects all domain-scoped artifacts and produces
the cross-domain views (C4 context, coupling matrix, target-state plan).

```
Phase 2 (current):                Phase 2 (sharded):

analyst/domain  ──┐               coordinator
analyst/flows   ──┼── full repo   ├─ assign Domain A,B → analyst/shard-1
analyst/tech    ──┘               ├─ assign Domain C,D → analyst/shard-2
                                  ├─ assign Domain E,F → analyst/shard-3
                                  └─ synthesis pass ── cross-domain artifacts
```

### Why this works

A 230k LOC monolith with 8 detected domains becomes 8 focused ~30k LOC investigations.
Each shard analyst works within the scale envelope that already performs well. The
synthesis pass only needs compact domain summaries as input — not the full raw investigation
context — so the architect sees a clean picture rather than a truncated one.

Tool responses are also smaller per shard because queries are scoped: `get_callers` on
methods in the Reservation domain returns only reservation-related callers, not the 118
call sites from the entire openmrs graph.

### What changes

**`pipeline/codedoc/stages/agent.py`**
- `run_supervisor_agent()`: detect repo size; for xlarge, run domain-sharding path
- New `_run_domain_coordinator()`: calls `get_domains`, partitions into N shards
- Replace `_run_analyst()` fan-out with N shard-analyst instances
- New synthesis pass: reads shard artifacts, produces C4 + coupling + target-state

**`pipeline/codedoc/prompts/`**
- New `analyst-shard.md`: domain-scoped Business Analyst prompt
- New `analyst-synthesis.md`: cross-domain synthesis prompt

**`pipeline/codedoc/artifact_planner.py`**
- New `xlarge-sharded` pipeline type with domain-scoped artifact sets

### Estimated impact

- Prune events: 22 → 2–3 per shard (each shard has bounded context growth)
- Target-state quality: high — synthesis pass reads clean domain summaries
- Time: similar total (N shards run in parallel); architect phase faster with compact input
- Applies to: repos with ≥4 detected Domain nodes

---

## Approach B — Two-Pass Architecture

### Concept

Split the pipeline into two sequential passes with different objectives:

**Pass 1 — Structural pass** (runs today, works at any scale):
Produces artifacts that derive from graph structure alone — ER diagram, coupling hotspots,
C4 context, module dependency map. These require wide but shallow graph queries and are
resilient to pruning because each query is self-contained.

**Pass 2 — Business logic pass** (domain-scoped):
Produces artifacts that require understanding business rules, validations, and workflows —
business capabilities, business journeys, target-state plan. These require deep, evidence-
based investigation. Run scoped to one domain at a time, sequentially or in parallel.

```
Pass 1 (structural — any scale):     Pass 2 (business logic — per domain):
  ER diagram                            for each Domain:
  Coupling hotspots                       business capabilities (this domain)
  C4 context                              workflows (this domain)
  Module dependency map                   target-state seams (this domain)
  Executive summary skeleton
```

### Why this matters

The structural artifacts are already excellent at xlarge. openmrs-core's coupling hotspots
(118 call sites, exact method names) and ER diagram (30+ JPA entities) were the highest-
quality outputs in the comparison. These don't need domain scoping — they benefit from
seeing the whole graph.

The business logic artifacts are where the context window fails. A single analyst trying
to write business capabilities for a 230k LOC system across 20 LLM turns with 22 prune
events produces thin output. The same analyst scoped to the Reservation domain (8 classes,
40 methods) produces detailed rules with evidence citations — as demonstrated by the
inventory-service runs where business capabilities quality is consistently high.

### What changes

**`pipeline/codedoc/stages/agent.py`**
- `run_supervisor_agent()`: for xlarge, run Pass 1 first, then Pass 2 per domain
- Pass 1 uses the existing 3-analyst fan-out scoped to structural artifacts only
- Pass 2 spawns one analyst per domain (capped at N concurrent), scoped to that domain

**`pipeline/codedoc/artifact_planner.py`**
- New `xlarge` artifact plan: structural artifacts in Pass 1, business artifacts in Pass 2
- Pass 2 plan is domain-count-aware (fewer domains = fewer Pass 2 runs)

**`pipeline/codedoc/prompts/`**
- `analyst-domain.md` gains a domain-scope parameter (inject domain members into prompt)
- New `analyst-structural.md`: pure graph-query prompt, no business rule investigation

### Estimated impact

- Pass 1: fast, reliable, same as today's structural quality
- Pass 2 per domain: prune events near zero (scoped investigation)
- Target-state quality: high — each domain seam is reasoned about in isolation
- Time: Pass 1 ~20 min + Pass 2 N×15 min (parallel); total similar to today's 97 min but
  with all artifacts completed

---

## Comparison

| Dimension | Domain Sharding (A) | Two-Pass (B) |
|---|---|---|
| Structural artifacts | ✅ Same as today | ✅ Dedicated pass |
| Business capabilities | ✅ Per-domain depth | ✅ Per-domain depth |
| Target-state plan | ✅ Synthesis pass | ✅ Per-domain seams → combined |
| Implementation complexity | Medium | Higher |
| Works without Domain nodes | ❌ Falls back to current | ✅ Pass 1 always runs |
| Incremental adoptability | Activates at xlarge only | Pass 1 can ship first |

**Recommendation:** Start with Approach A (domain sharding). It is the minimal change that
addresses the core problem — too much context per analyst. Approach B is strictly more
powerful but requires splitting the artifact contract into two passes, which is a larger
architectural change. Both approaches are mutually compatible and can be layered.

---

## Prerequisites

Both approaches depend on Domain nodes being present in the graph (produced by
`DomainDetector` post-processing in the indexer). Repos indexed without the latest
`code-mem-graph.jar` (rebuilt via `make lumen-install`) will have no Domain nodes and
must fall back to the current single-pass approach.

Check with:
```cypher
MATCH (d:Domain) RETURN count(d);
-- Should be > 0 for post-processing to have run
```

---

## Not included in this document

Short-term mitigations already implemented:

- Adaptive context pruning (3 turns at once for large/xlarge)
- Adaptive architect char limit (80k total / artifact count)
- `api-spec.yaml` conditional for large/xlarge
- Pre-fetched domain/workflow data in orientation
- Artifact path validation

These reduce failure rates but do not eliminate the fundamental context window constraint
on repos >200k LOC. The structural approaches above address that constraint.
