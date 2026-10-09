# Kotlin tree-sitter indexer increment — design

## Context

`treesitter-indexer/` (standalone top-level engine, targeting LadybugDB) covers Java, Python,
PHP, and JS/TS/React, each wired into `lumen run` behind its own
`--experimental-treesitter-<language>` flag. This is the final increment in the originally
planned 4-language rollout: Kotlin.

Full background on the old Kotlin parser's confirmed gaps (zero annotation-usage extraction,
zero Ktor support, undeclared-but-unpopulated `SEALED_SUBTYPE`/`SUSPENDS`/`Lambda`) and the
overall roadmap is in `backlog/treesitter-indexer-remaining-languages.md` — this doc does not
repeat that material, only the decisions specific to this increment.

Smaller in scope than JS/TS/React (closer to PHP's size): per the backlog's own research, most
of the structural work is "populate node/rel tables that already exist in `graph_store.py`'s
schema but are currently unpopulated" (`DataClass`, `SealedClass`, `SealedInterface`,
`ObjectDecl`, `CompanionObject`, `ExtensionFunction`, `SuspendFunction`, `Property`, plus rel
tables `SEALED_SUBTYPE`, `DELEGATES_TO`, `COMPANION_OF`, `SUSPENDS`, `EXTENSION_OF`), ported
from the old PSI-based `KotlinSourceParser.java`'s already-proven structural model — not new
schema design. Exactly two pieces are genuinely new design work.

## Scope

In scope:
- Structural extraction on `tree-sitter-kotlin`: classes, data classes, sealed
  classes/interfaces, object declarations, companion objects, extension functions, suspend
  functions, properties (including delegation, `DELEGATES_TO`) — targeting the already-declared
  tables listed above.
- CALLS confidence tiers (0.95/0.90/0.50), ported fresh following the established per-language
  recipe (same-corpus type index), not shared code with Java/Python/PHP/JS.
- **Annotation-usage extraction** (new, the single highest-value item): a `visitAnnotations()`-
  equivalent walk over every declaration type (classes, methods, constructors, properties),
  emitting ordinary `HAS_ANNOTATION` edges — the exact mechanism Java already has and Kotlin
  never got. Once populated, `resolve_route()`/`layer_from_annotation()` work for
  Spring/JAX-RS-annotated Kotlin with **zero** further postprocess/ changes, mirroring Java
  exactly (no `build_kotlin_workflows` needed at all if Kotlin's Method/Class/Constructor nodes
  are typed compatibly with Java's — confirm this during planning before assuming it).
- **Ktor's call-based routing DSL** (new): `routing { get("/path") { ... } }`-shaped recognition
  feeding the existing `postprocess/call_routes.py::resolve_call_route()` unchanged — same
  split already proven twice (PHP's Laravel, JS's Express/Fastify): recognition is
  language/framework-specific and lives in the extractor; verb/path resolution is generic and
  already built.
- `SEALED_SUBTYPE`: attempted as a plain syntactic feature, not deferred — a class/object
  declaring `: SealedTypeName` is the same detection EXTENDS/IMPLEMENTS already does; when the
  target resolves to an internal `SealedClass`/`SealedInterface`, additionally emit
  `SEALED_SUBTYPE` alongside the normal `EXTENDS`/`IMPLEMENTS` edge. (The old engine's comment
  calling this "needs more sophisticated analysis" is not treated as evidence it's actually
  hard — no attempt to justify that call was found during research; this increment attempts the
  straightforward syntactic version and documents whatever real limitation surfaces, rather than
  deferring on an unverified assumption.)
- `SUSPENDS`: attempted as a plain syntactic feature — an ordinary CALLS-shaped edge between two
  `SuspendFunction`-typed nodes, emitted alongside (not instead of) the normal CALLS edge,
  reusing the same confidence-tier resolution already built for CALLS.

Explicitly out of scope (deferred, not forgotten):
- Dedicated `Lambda` nodes — the old engine never gave lambdas their own nodes either (bodies
  attributed to the enclosing function); this increment keeps that same accepted divergence
  rather than introducing new lambda-level modeling, since it isn't one of the two flagged
  high-value items and doesn't block them.
- Any fix to the *old* Kotlin engine's suspected `CALLS` persistence bug (`resolved` vs.
  `confidence`/`reason` schema mismatch) — tracked separately in the backlog, independent,
  decoupled fix to the currently-shipping engine, not part of this new-extractor work.
- Receiver-type or overload-aware call resolution beyond what every other language's
  same-corpus recipe already does (no new tier, no new type-inference beyond the established
  `this`/same-file/import-resolved/global shape).

## Components

### `treesitter-indexer/kotlin/extractor.py` + `kotlin/calls.py`

Mirrors `php/extractor.py`/`php/calls.py` and `js/extractor.py`/`js/calls.py` in shape and
style: a tree-sitter AST walk producing nodes/rels in the existing canonical schema, a
same-corpus type index for CALLS resolution, and annotation-usage + Ktor-routing recognition
kept in `calls.py` alongside CALLS (same rationale as every prior increment: all three need the
same class/method-lookup machinery).

### Annotation-usage extraction

Lives in the extractor's declaration-walking code (not a separate module) — for every
class/method/constructor/property declaration, read its annotation list (Kotlin's
`@Annotation(args)` syntax) and emit `HAS_ANNOTATION` edges exactly like Java's extractor
already does. No new resolver needed; `resolve_route()`/`layer_from_annotation()` consume this
identically regardless of source language.

### Ktor routing

New recognition logic in the extractor (mirrors PHP's `_is_route_facade_call`/JS's
`_is_express_route_call` shape): recognize Ktor's `get(path) { ... }`/`post(path) { ... }`-style
calls inside a `routing { ... }` block, feed the verb + path to the existing
`resolve_call_route()`, and — following the same pattern established for both prior call-based
routing mechanisms — convert the resolved result into an ordinary `HAS_ANNOTATION` edge at
extraction time so `resolve_route()` and any workflow-building code stay uniform regardless of
whether a route came from an annotation or a call.

### `postprocess/workflow_builder.py`

Decide during planning (not here) whether Kotlin's Spring/JAX-RS case needs a new
`build_kotlin_workflows` or can reuse `build_jvm_workflows` directly — contingent on whether
Kotlin's `Method`/`Class`/`Constructor` nodes are typed identically to Java's. If reuse isn't
possible (e.g. Kotlin's own callable tables need to participate, same reason JS needed
`Function`/`ArrowFunction`/`Component`/`Hook` in its BFS), write a parallel function, never a
modification to the existing one.

## Data flow

```
Kotlin source
  → kotlin/extractor.py (tree-sitter walk)
      → structural nodes/rels (Class/DataClass/SealedClass/ObjectDecl/CompanionObject/
        ExtensionFunction/SuspendFunction/Property/...)
      → annotation-usage walk → HAS_ANNOTATION edges
      → CALLS edges via kotlin/calls.py (3-tier confidence), including SUSPENDS alongside
        suspend-to-suspend CALLS edges
      → EXTENDS/IMPLEMENTS, additionally tagged SEALED_SUBTYPE when the target is sealed
      → Ktor routing call-sites recognized → resolve_call_route() → HAS_ANNOTATION
  → graph_store.py (LadybugDB write)
  → postprocess/workflow_builder.py: build_jvm_workflows() (if reusable) or a new
    build_kotlin_workflows() → Workflow/WORKFLOW_STEP nodes
  → postprocess/domain_detector.py: detect_domains() (verify during planning whether any
    Kotlin-specific query union is needed, same check already done for Python/PHP/JS)
```

## Error handling

Same posture as every prior increment: best-effort, syntactic-only resolution; unresolvable
calls/routes/sealed-subtypes are simply not emitted. Any schema combo discovered missing during
real fixture testing gets a pure additive fix with a regression test, same as every prior
increment (all four so far have hit at least one real schema gap this way).

## Testing

- Unit tests mirroring the existing per-language test files' structure: structural extraction
  (data classes, sealed types, object declarations, companion objects, extension functions,
  suspend functions, property delegation), CALLS tier cases, annotation-usage cases (proving
  the new Spring/JAX-RS-on-Kotlin entry-point detection actually works), Ktor routing cases,
  `SEALED_SUBTYPE`/`SUSPENDS` cases.
- New fixture `e2e-test/fixtures/mini-kotlin-service/`, exercising every gap this increment
  claims to close, same spirit as `mini-php-service`/`mini-express-service` — including at
  least one entry point that produces a real (>=0.9) multi-hop Workflow trace (every prior
  fixture needed deliberate attention to this; don't assume it falls out for free).
- `treesitter-indexer/scripts/compare_graphs_kotlin.py`, same asymmetric-diff pattern as the
  three prior comparison scripts — the old engine's zero-annotation-usage/zero-Ktor gaps mean
  most interesting signals will be "expected new-only."
- End-to-end: `lumen run --experimental-treesitter-kotlin` (exact flag/binary naming to be
  confirmed against the pipeline's actual Kotlin language-category key during planning, same
  verification already done for JS's `"js"` key — do not assume `"kotlin"` without checking)
  against the new fixture, inspected via `scripts/inspect_graph.py`.

## Open implementation details for the plan (not design decisions — flagged so the plan doesn't
have to rediscover them)

- Exact `tree-sitter-kotlin` API shape (language function name, node/field names for
  annotations, sealed-class syntax, companion objects, suspend modifiers) — verify empirically
  before writing extraction code, same as every prior grammar.
- Exact flag/binary naming — confirm the pipeline's real Kotlin language-category key in
  `pipeline/codedoc/language_registry.py` before assuming `--experimental-treesitter-kotlin`/
  `cmg-kotlin-ts` are the right names.
- Whether `build_jvm_workflows` is directly reusable for Kotlin's Spring/JAX-RS case or needs a
  parallel `build_kotlin_workflows` — decide with the actual node-table shapes in front of the
  planner, same kind of call every prior increment's workflow-builder section made.
