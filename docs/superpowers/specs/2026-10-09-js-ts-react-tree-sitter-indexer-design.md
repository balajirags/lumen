# JS/TS/React tree-sitter indexer increment — design

## Context

`treesitter-indexer/` (standalone top-level engine, targeting LadybugDB) covers Java, Python,
and PHP, each wired into `lumen run` behind its own `--experimental-treesitter-<language>` flag.
This is the next increment: JavaScript, TypeScript, and React — the largest and riskiest of the
four remaining languages, bundling four largely independent problems (CALLS bug fixes, real
TypeScript type extraction, a React component-detection design decision, and backend routing)
behind two grammars for the first time in this project.

Full background on the old JS indexer's confirmed bugs (the `this.method()` CALLS
id-construction bug, missing 0.50 tier, schema/emission mismatches silently dropping edges,
zero backend-framework detection, dropped TypeScript `isInterface` flag) and the overall
4-language roadmap is in `backlog/treesitter-indexer-remaining-languages.md` — this doc does not
repeat that material, only the decisions specific to this increment. Per user decision, this
increment covers all five goal items in one pass (matching the ambition level of the Java,
Python, and PHP increments), rather than splitting React/TS-types/routing into later increments.

## Scope

In scope:
- Structural extraction for both JavaScript and TypeScript, selected per file by extension:
  `tree-sitter-javascript` for `.js`/`.jsx`, `tree-sitter-typescript`'s two language functions
  for `.ts`/`.tsx`. Classes, functions, arrow functions, modules — targeting the existing
  `Module`/`Function`/`ArrowFunction`/`Class`/`Interface` tables already declared in
  `graph_store.py`.
- Real TypeScript type extraction: interface method signatures (not just property signatures,
  closing the old engine's gap), a persisted `isInterface`/`isClass` distinction (the old
  engine's `isInterface` flag was dropped by `store.js`'s write-allowlist — this engine's shared
  `type` column, already used for PHP's trait-marking, covers this with zero new schema), type
  aliases, and generics (at least capturing the base type name, e.g. `Array<User>` → `Array`,
  matching what's structurally reasonable without a full generics model).
- CALLS confidence tiers (0.95/0.90/0.50), explicitly fixing `this.method()` id construction
  and aligning every emitted shape with its declared schema combo before writing, rather than
  discovering missing combos via silent failures (the Java/Python/PHP increments all hit real
  schema gaps this way — this increment should enumerate needed combos up front where
  practical, while still treating any gap found via real fixture testing as a normal additive
  schema fix, same as every prior increment).
- React component/hook modeling via the existing PascalCase/`use*` naming heuristic (not
  JSX-return-type detection) — targeting the existing `Component`/`Hook`/`RENDERS`/`USES_HOOK`
  tables already declared in `graph_store.py`.
- Backend routing: Express/Fastify-style call-based routing (`router.get(path, handler)`) via
  the existing `postprocess/call_routes.py::resolve_call_route()`, with zero changes to that
  function — only new recognition logic in the JS extractor, mirroring PHP's
  `_is_route_facade_call`-style pattern. NestJS's decorator-based routes (`@Get()`) via a new
  JS decorator-to-`HAS_ANNOTATION` emission path, reusing `resolve_route()` unchanged.
- A dedicated fixture (or fixtures) under `e2e-test/fixtures/` and `compare_graphs_js.py`,
  first validation harness for this language family.

Explicitly out of scope (deferred, not forgotten):
- Next.js file-based API routing (`pages/api/*.js` / `app/api/*/route.js`) — a third routing
  shape (filesystem convention, not a call or a decorator) genuinely different from both
  mechanisms this increment builds. Tracked as a follow-up once this increment's two mechanisms
  are proven; not blocking.
- `tsconfig.json` path-alias resolution and `node_modules` import resolution for CALLS — the
  old engine never had these either; this increment's import resolution stays relative-path-only
  plus the new same-corpus global fallback, same posture as every prior increment's documented
  scope boundaries.
- The stale `CLAUDE.md` route-map/component-boundaries suppression claim (confirmed inaccurate
  during research) — a separate, small, independent documentation fix, unrelated to this
  engine's correctness.

## Components

### `treesitter-indexer/javascript/extractor.py` + `javascript/calls.py`

Mirrors `php/extractor.py`/`php/calls.py` in shape and style, adapted for two grammars: a
per-file dispatch (by extension) to the right `tree_sitter.Language`, then a unified AST walk
producing nodes/rels in the existing canonical schema. `calls.py` carries the same-corpus type
index and the three confidence tiers, with the `this.method()` fix applied from the start
(resolving against the enclosing class's own method index, not a wrong-shaped id).

### React/TypeScript-specific extraction (same file, not a separate module)

Component/Hook detection (naming-heuristic) and TypeScript-specific nodes (interfaces, type
aliases, generics) live in `javascript/extractor.py` directly rather than spawning
sub-modules — each is a small, targeted addition to the same AST walk, not an independent
concern the way CALLS resolution or routing recognition are. This mirrors how Python's
decorator capture and PHP's trait-marking were folded into their respective `extractor.py`
files rather than split out.

### Backend routing

Express/Fastify call-site recognition (new, in `javascript/extractor.py`) feeds the *existing*
`resolve_call_route()` exactly as PHP's recognition code does — zero changes to
`postprocess/call_routes.py`. NestJS decorator recognition (also new, in the extractor) emits
ordinary `HAS_ANNOTATION` edges, reusing `resolve_route()` unchanged, the same pattern PHP's
`_emit_route_annotation` established for call-based routes converted to annotation-shaped data.

### `postprocess/workflow_builder.py`

New `build_js_workflows(store)`, parallel to `build_jvm_workflows`/`build_python_workflows`/
`build_php_workflows` — not a modification of any of them.

## Data flow

```
JS/TS/JSX/TSX source
  → javascript/extractor.py (grammar selected per file by extension)
      → structural nodes/rels (Class/Interface/Function/ArrowFunction/Component/Hook/...)
      → CALLS edges via javascript/calls.py (3-tier confidence, this.method() fixed)
      → Express/Fastify routing call-sites recognized → resolve_call_route() → HAS_ANNOTATION
      → NestJS decorator routes recognized → HAS_ANNOTATION directly
  → graph_store.py (LadybugDB write)
  → postprocess/workflow_builder.py: build_js_workflows() → Workflow/WORKFLOW_STEP nodes
  → postprocess/domain_detector.py: detect_domains() (verify during planning whether any
    JS-specific query union is needed, same check already done for Python/PHP)
```

## Error handling

Same posture as every prior increment: best-effort, syntactic-only resolution; unresolvable
calls/routes are simply not emitted. Any schema combo discovered missing during real fixture
testing (as happened at least once in every prior increment) gets a pure additive fix with a
regression test, same as Java's/Python's/PHP's.

## Testing

- Unit tests mirroring the existing per-language test files' structure: structural extraction
  (both `.js` and `.ts`/`.tsx` cases), CALLS tier cases (including a dedicated case proving the
  `this.method()` fix), React component/hook detection cases, Express/Fastify and NestJS routing
  cases, `build_js_workflows` cases.
- New fixture(s) under `e2e-test/fixtures/` (naming TBD during planning — likely two small
  fixtures, one backend-flavored for routing/CALLS and one React-flavored for
  component/hook detection, rather than forcing both into one unrealistic file), exercising
  every gap this increment claims to close, same spirit as `mini-php-service`.
- `treesitter-indexer/scripts/compare_graphs_js.py`, same asymmetric-diff pattern as
  `compare_graphs_python.py`/`compare_graphs_php.py` — the old engine's near-total CALLS/routing
  gaps mean most interesting signals will be "expected new-only."
- End-to-end: `lumen run --experimental-treesitter-js` (or `--experimental-treesitter-javascript`
  — exact flag name TBD during planning, consistent with existing flag-naming conventions)
  against the new fixture(s), inspected via `scripts/inspect_graph.py`.

## Open implementation details for the plan (not design decisions — flagged so the plan doesn't
have to rediscover them)

- Exact `tree-sitter-javascript`/`tree-sitter-typescript` API shape (language function names,
  whether JSX support is built into the base JS grammar or needs the dedicated TSX grammar for
  `.jsx` too) — verify empirically before writing extraction code, same as PHP's grammar was.
- Exact flag/binary naming (`cmg-js-ts` vs `cmg-javascript-ts`, `--experimental-treesitter-js`
  vs `-javascript`) — pick one consistent with the existing `java`/`python`/`php` naming.
- Whether NestJS decorator recognition needs a new `Decorator`-shaped intermediate (like
  Python's) or can emit `HAS_ANNOTATION` directly at extraction time (like PHP's routes did) —
  decide with the actual schema in front of the planner, same kind of call PHP's plan made.
