# PHP tree-sitter indexer increment — design

## Context

`treesitter-indexer/` (standalone top-level engine, targeting LadybugDB) already covers Java
and Python, each wired into `lumen run` behind its own `--experimental-treesitter-<language>`
flag. This is the next increment in that rollout: PHP, chosen second (of the four remaining
languages) because its structural gap is small and well-scoped, and because Laravel's
call-based `Route::get($path, $handler)` routing is the cheapest, best-understood place to
first build a mechanism that JS/TS/React (Express/Fastify/NestJS) and Kotlin (Ktor) will both
need later.

Full background on the old PHP indexer's confirmed gaps, the overall 4-language roadmap and
its ordering rationale, and the conventions established by the Java/Python increments (generic
detection philosophy, fixture/validation pattern, kuzu/ladybug process-isolation constraint,
commit conventions) is already written down in
`backlog/treesitter-indexer-remaining-languages.md` — this doc does not repeat that material,
only the decisions specific to this increment.

## Scope

In scope:
- Structural extraction: classes, interfaces, traits (`phpKind: 'trait'`), methods,
  constructors, properties, class constants — matching the old `nikic/php-parser`-based
  engine's coverage.
- Two structural gaps the old engine never closed: `Parameter`/`HAS_PARAMETER` and `RETURNS`,
  from PHP 7.4+ type hints.
- CALLS confidence tiers: the old engine's existing two tiers (0.95 for
  `$this->`/`self::`/`static::` calls, 0.90 for static-class/free-function/`new` calls) plus a
  new 0.50 global-name-index fallback (same shape as Java's and Python's).
- A new, generalized `resolve_call_route(calleeName, args)` resolver for call-based routing
  DSLs, applied here to Laravel's `Route::get/post/put/delete/patch/any/match(...)`.
- Generic HTTP-dependency detection reuse (`http_url_detection.py`'s `looks_like_url()` —
  already promoted to a shared module during the Python increment, no further work needed).
- A dedicated fixture (`e2e-test/fixtures/mini-php-service/`) and `compare_graphs_php.py`.
- Pipeline wiring: `--experimental-treesitter-php` CLI flag, `should_use_treesitter_php()`,
  dispatch to `cmg-php-ts`, mirroring the existing Java/Python wiring exactly.

Explicitly out of scope (deferred, not forgotten — tracked in the backlog file):
- `HttpSuperglobalDetector`-equivalent raw-PHP (`$_GET`/`$_POST`/`$_REQUEST`) entry-point
  detection. This is a different detection mechanism (data-flow tracing into superglobal
  access, not call-site routing) and isn't the primary gap this increment targets.
- Any fix to the *old* PHP engine's bugs (`phpKind` silently dropped by the shared store's
  write-allowlist) — independent, already-tracked, decoupled fix.

## Components

### `treesitter-indexer/php/extractor.py` + `php/calls.py`

Mirrors `java/extractor.py`/`java/calls.py` and `python/extractor.py`/`python/calls.py` in
shape and style: a tree-sitter AST walk (grammar: `tree-sitter-php`) producing nodes/rels in
the existing canonical schema (`graph_store.py`), plus a same-corpus type index (`calls.py`)
resolving CALLS into the three confidence tiers described above.

### `treesitter-indexer/postprocess/call_routes.py` (new)

A sibling to `postprocess/http_routes.py`, not a modification of it — the two modules handle
genuinely different input shapes (annotation name/value pairs vs. call-site callee name +
argument list), so keeping them separate avoids `http_routes.py` growing a second, unrelated
responsibility.

```
resolve_call_route(callee_name: str, args: list) -> tuple[str, str] | None
```

Internally: tokenizes the callee's method-name segment for an HTTP verb using the *same*
tokenization/vocabulary-matching approach `annotation_http_verb()` already uses (reused
directly, not reimplemented — `postprocess/http_routes.py`'s `_tokenize()`/verb vocabulary are
language- and input-shape-agnostic already). The first string-literal argument is treated as
the route path. Designed generically from day one (per the approved design decision): it takes
a plain callee name string and a plain argument list, with no Laravel-specific code in the
resolver itself — Laravel-specific behavior (recognizing `Route::` as the receiver, walking
`->middleware(...)->group(closure)` chains, resolving `[Controller::class, 'method']`/
`'Controller@method'` handler forms into a target reference) lives in `php/extractor.py`'s
call-site walk, which is responsible for recognizing *which* calls are routing calls and
handing `resolve_call_route` the normalized `(calleeName, args)` pair. This mirrors how
`resolve_route()` doesn't know anything about Spring vs. Micronaut vs. JAX-RS — it only
tokenizes whatever annotation name/value it's handed.

This split is what makes the mechanism reusable for JS (Express/Fastify's `router.get(path,
handler)`) and Kotlin (Ktor's `get(path) { ... }`) later: only the call-site recognition logic
in each language's own extractor needs to change; `resolve_call_route` itself should need zero
changes.

### `treesitter-indexer/postprocess/workflow_builder.py`

New `build_php_workflows(store)`, parallel to `build_jvm_workflows` and
`build_python_workflows` — not a modification of either. Its route-gathering query reads
call-site routing data (however `php/extractor.py` persists it — likely as a lightweight
`RouteCall`-shaped intermediate, or directly as `HAS_ANNOTATION`-equivalent synthetic edges if
that turns out simpler; this is an implementation detail to resolve during the actual plan, not
this design doc) and feeds it through `resolve_call_route`.

## Data flow

```
PHP source
  → php/extractor.py (tree-sitter walk)
      → structural nodes/rels (Class/Interface/Method/Parameter/...)
      → CALLS edges via php/calls.py (3-tier confidence)
      → routing call-sites recognized (Route::get/post/... incl. middleware/group forms)
  → graph_store.py (LadybugDB write)
  → postprocess/call_routes.py: resolve_call_route() per recognized call-site
  → postprocess/workflow_builder.py: build_php_workflows() → Workflow/WORKFLOW_STEP nodes
  → postprocess/domain_detector.py: detect_domains() (no changes needed — already
    language-agnostic via the `language` property on clusterable nodes, same as Python)
```

## Error handling

Same posture as Java/Python: best-effort, syntactic-only resolution; unresolvable calls/routes
are simply not emitted (no placeholder/inferred nodes for routing, matching the old PHP
engine's existing CALLS behavior of only emitting edges when the target already exists).
`resolve_call_route` returns `None` for anything it can't confidently classify as a route,
rather than guessing.

## Testing

- Unit tests (`treesitter-indexer/tests/test_php_extractor.py`, `test_call_routes.py`,
  `test_php_postprocess.py`), mirroring the existing Python test files' structure: structural
  extraction cases, CALLS tier cases (including one proving the new 0.50 fallback fires),
  `resolve_call_route` cases (plain route, `->middleware()->group()` nesting, both handler
  forms), `build_php_workflows` cases.
- New fixture `e2e-test/fixtures/mini-php-service/`: a Laravel-style app with — at minimum —
  one plain route, one `Route::middleware('auth')->group(closure)` block with 2+ nested routes,
  one `[Controller::class, 'method']` handler and one `'Controller@method'` string handler, one
  typed method signature (to exercise `Parameter`/`HAS_PARAMETER`/`RETURNS`), and one call
  shape that only resolves via the new 0.50 CALLS tier.
- `treesitter-indexer/scripts/compare_graphs_php.py`, same asymmetric-diff pattern as
  `compare_graphs_python.py`: list the specific tables/edges the old engine never produces
  (`Parameter`, `HAS_PARAMETER`, `RETURNS`, `WORKFLOW_STEP` for routes inside middleware groups)
  and assert zero-on-old/non-zero-on-new rather than treating any mismatch as a regression.
- End-to-end: `lumen run --experimental-treesitter-php` against the new fixture, inspected via
  `scripts/inspect_graph.py`, mirroring the Python increment's verification.

## Open implementation details for the plan (not design decisions — just flagged so the plan
doesn't have to rediscover them)

- Exact persisted shape for "this call-site is a routing call" between `php/extractor.py` and
  `build_php_workflows`/`resolve_call_route` — a new lightweight edge/property, or reuse of an
  existing generic mechanism. Should be decided with the actual schema in front of the planner.
- Whether `[Controller::class, 'method']` handler resolution needs the same same-corpus type
  index `php/calls.py` already builds (likely yes — it's the same "resolve a reference to an
  internal class+method" problem CALLS resolution already solves).
