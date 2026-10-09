# Tree-sitter indexer: remaining languages (Kotlin)

## How to resume this work (read this first)

Everything below is written to be self-contained — you shouldn't need to re-read old
conversation history to pick this back up. Start here:

0. **Current uncommitted state**: as of the JS/TS/React increment's completion, everything
   relevant is committed. `backlog/incremental-reindexing.md` and
   `backlog/multi-repo-cross-service-graph.md` remain untracked but **unrelated** to this
   indexer work (pre-existing scratch files from earlier work on this branch) — don't assume
   they belong together. `indexer/parsers/python/__pycache__/store.cpython-311.pyc` may show
   modified (stray bytecode cache from running the *old* Python indexer's tests) — unrelated,
   leave alone.
1. `git checkout feature/treesitter-java-indexer` (all of this work — Java, Python, PHP,
   JS/TS/React, and the final Kotlin increment — lives on this one branch; the name is stale
   now that it covers more than Java, not worth renaming mid-flight).
2. `git log --oneline -30` to see what's landed. As of this writing, the newest relevant commit
   is `143ca70` (JS/TS/React pipeline wiring); that increment spans roughly `de0ff1d`..`143ca70`,
   with `baba1a5`..`2063a65` being the earlier PHP increment and `85875e1` being Python. Design+
   plan docs for each live under `docs/superpowers/specs/` and `docs/superpowers/plans/`
   (`2026-10-09-js-ts-react-tree-sitter-indexer-{design,plan}...` for the most recent — good
   reference for how much detail to put in Kotlin's plan).
3. Sanity-check the existing engine still works before adding anything new:
   ```bash
   cd treesitter-indexer
   .venv/bin/python3 -m unittest discover -s tests   # expect 120 passed, 0 failed
   ```
   **Gotcha**: you must use `treesitter-indexer/.venv/bin/python3`, not a bare `python3` —
   plain `python3` will raise `ModuleNotFoundError: No module named 'ladybug'` even though the
   tests look like they should pass. If `.venv/` is missing, run `./install.sh` first.
4. To see the engine actually working end-to-end on an example fixture:
   ```bash
   .venv/bin/python3 parse_python.py ../e2e-test/fixtures/mini-python-service \
     --db-path /tmp/scratch-db --repo-name mini-python-service --clear
   .venv/bin/python3 scripts/inspect_graph.py /tmp/scratch-db/mini-python-service-db
   ```
   (swap `parse_python.py` / the fixture path for `parse.py` + `mini-spring-app` to see the
   Java engine instead). `inspect_graph.py` prints entry points, Workflows, Domains, and sample
   CALLS edges with their confidence tier — the fastest way to eyeball whether a new language's
   extraction is producing real signal, without going through `lumen run` or an LLM at all.
5. `pipeline/tests/test_indexer.py` has `should_use_treesitter_java`/`should_use_treesitter_python`
   tests — the CLI/pipeline wiring pattern (`--experimental-treesitter-<language>` flag →
   `should_use_treesitter_<language>()` gate → dispatch to `cmg-<language>-ts` binary instead of
   the old one) is meant to be copied exactly for each new language; see
   `pipeline/codedoc/stages/indexer.py` and `pipeline/codedoc/cli.py` for the two existing
   examples to mirror.
6. When starting the next language's increment (PHP first, per the recommended order below),
   run a fresh `/plan` for it rather than trying to implement straight from this backlog file —
   this file is the research/design *input* to that plan, not a replacement for planning. The
   Python increment's plan (written the same way) is a good template for how much detail to
   put in a from-scratch language-increment plan.

## Conventions established so far (apply to every future increment)

These aren't arbitrary style points — several came from explicit corrections or hard-won
bugs during the Java and Python increments, and violating them either breaks a working pattern
or reintroduces an already-fixed class of bug.

- **Detect by structural/lexical shape, never by hardcoding a specific framework/library name.**
  This was an explicit, mid-session user correction during the Java increment ("do not hardcode
  feign client or anything on framework/library, the tools should be generic") and is now the
  core design philosophy of the whole engine. Concretely: `postprocess/http_routes.py`'s
  `annotation_http_verb()` tokenizes an annotation/decorator *name* and checks tokens against a
  small closed vocabulary (HTTP verbs, route-hint words) — it works for Spring, Micronaut,
  JAX-RS, FastAPI, and made-up frameworks alike, with zero per-framework code.
  `postprocess/layer_classification.py` does the same for architectural-layer words
  (controller/service/repository/etc.). `http_url_detection.py`'s `looks_like_url()` flags any
  `http(s)://`-shaped string literal as a dependency, regardless of which method or annotation
  it appears in. **Every future language's routing/dependency/layering detection should follow
  this same shape** — tokenize + vocabulary-match, not a hardcoded per-framework name list.
- **New extractors never touch or modify the old `indexer/app/`, `indexer/parsers/`, or their
  KuzuDB writers.** `treesitter-indexer/` is fully additive and decoupled; the old engines keep
  running unchanged until/unless a repo opts into the new one via an
  `--experimental-treesitter-<language>` flag. Bugs found in the *old* engines during this
  research (see the bottom section) are tracked as separate, independent fixes — never folded
  into a new-extractor increment's scope.
- **Every language gets its own dedicated fixture, never mutate an existing one.**
  `e2e-test/fixtures/mini-flask-app/` and other pre-existing fixtures are wired into
  `e2e-test/test-lumen.sh`'s regression suite for the *old* engines — changing them risks
  breaking that suite. Instead, build a new, separate fixture per language (e.g.
  `mini-spring-app` for Java, `mini-python-service` for Python — the latter was explicitly built
  as an extended copy of `mini-flask-app`, not a mutation of it) used only by that language's
  `compare_graphs_<language>.py` script, never added to `test-lumen.sh`.
- **CALLS confidence-tier recipe, ported fresh per language, not shared code**: a same-corpus
  type index (internal types, per-type field-type map, per-function/method local-var-type map,
  import map) resolving calls into three tiers — a high-confidence tier for same-file/same-scope
  calls (0.95), a medium tier for import-resolved calls (0.90), and a global name-index fallback
  (0.50) for anything else that matches exactly one function/method name anywhere in the corpus.
  Java and Python each implement this independently in their own `calls.py` (not a shared
  module) because the actual resolution rules are language-specific (e.g. Python's needs to
  handle `self.method()` and constructor-inferred object types; Java's needs generics/interface
  resolution) — but the *shape* (three tiers, same confidence values, `reason` string explaining
  which tier fired) should stay identical so `postprocess/domain_detector.py`'s
  `WHERE c.confidence >= 0.9` filter and similar downstream queries work unchanged for every
  language.
- **Validation pattern**: a `compare_graphs_<language>.py` script (subprocess-isolated — see
  next bullet) runs both the old and new engine against the shared fixture and diffs counts.
  Unlike a same-language regression check, these comparisons are *intentionally asymmetric*:
  list out the specific tables/edges the old engine is known to never produce (e.g. Python's
  `Parameter`/`RETURNS`/`WORKFLOW_STEP`, or the specific previously-unresolvable CALLS edges)
  and assert they're zero-on-old/non-zero-on-new, rather than treating any mismatch as a
  regression. `treesitter-indexer/scripts/compare_graphs_python.py` is the reference example.
- **`kuzu` (used by the old engines) and `ladybug` (used by the new engine) cannot coexist in
  one Python process, in either import order, even after a failed open attempt** — this was
  verified empirically, not assumed. Any tooling that needs to read both an old-engine DB and a
  new-engine DB (like the `compare_graphs_*.py` scripts) must dispatch each read to a *separate
  subprocess* via `scripts/_dump_graph.py --backend kuzu|ladybug`. Never try to import both in
  one script.
- **Commit messages: one line only, no body, no `Co-Authored-By:` trailer** — a standing,
  previously-corrected-twice convention for this project, overriding any harness default.
- **Never commit without being explicitly asked.** Implement + verify, then report status and
  wait for an explicit "commit this" before running `git commit`.

## Status

`treesitter-indexer/` (new, standalone, top-level engine targeting LadybugDB instead of the
archived `kuzu` package) currently covers:
- **Java** — complete, on `feature/treesitter-java-indexer`, wired into `lumen run` behind
  `--experimental-treesitter-java`. Includes generic (non-framework-hardcoded) HTTP route
  detection (`postprocess/http_routes.py`), architectural-layer classification
  (`postprocess/layer_classification.py`), config extraction, generic HTTP-dependency
  detection (`http_url_detection.py`).
- **Python** — complete, same branch, wired behind `--experimental-treesitter-python`
  (commit `85875e1`). Closed the two biggest documented gaps in the old `indexer/parsers/python/`
  engine: `WorkflowBuilder` had zero Python strategy (Flask/FastAPI produced no workflow
  traces at all), and CALLS resolution missed all non-`self` object calls. New CALLS has three
  confidence tiers (0.95 same-file / 0.90 import-resolved / 0.50 global fallback, the last one
  being new for this language and disproportionately valuable given Python's dynamic typing).
  Validated against a new `e2e-test/fixtures/mini-python-service/` fixture and
  `scripts/compare_graphs_python.py` (PASS).
- **PHP** — complete, same branch, wired behind `--experimental-treesitter-php`. Full detail
  (including the real bugs found via TDD, all fixed) is in the "PHP — done" section below;
  commits span `baba1a5` (tree-sitter-php dependency) through `2063a65` (pipeline wiring), plus
  design/plan docs at `docs/superpowers/specs/2026-10-09-php-tree-sitter-indexer-design.md` and
  `docs/superpowers/plans/2026-10-09-php-tree-sitter-indexer.md`. 89 unit tests pass (up from
  60 pre-PHP); `scripts/compare_graphs_php.py` reports PASS; `lumen run --experimental-treesitter-php`
  verified end-to-end.
- **JS/TS/React** — complete, same branch, wired behind `--experimental-treesitter-js` (the
  pipeline's existing JS/TS/JSX/TSX/MJS/CJS language-category key is `"js"`, confirmed in
  `pipeline/codedoc/language_registry.py` — NOT `"javascript"`). Full detail (including 6 real
  bugs found via TDD, all fixed) is in the "JS/TS/React — done" section below; design+plan docs
  at `docs/superpowers/specs/2026-10-09-js-ts-react-tree-sitter-indexer-design.md` and
  `docs/superpowers/plans/2026-10-09-js-ts-react-tree-sitter-indexer.md`. 120 unit tests pass
  (up from 89 pre-JS); `scripts/compare_graphs_js.py` reports PASS on both new fixtures;
  `lumen run --experimental-treesitter-js` verified end-to-end.

**Recommended remaining order: Kotlin** (the only language left). Lowest new-design risk for
its dominant case (Spring/JAX-RS-annotated JVM backends — near-exact repeat of Java's
`HAS_ANNOTATION`/`resolve_route` pattern), but the largest raw grammar surface (data classes,
sealed types, coroutines, delegation), and a narrower deployment surface (non-Android JVM
backends only) than Python/JS/PHP's near-universal presence. Ktor's call-based routing DSL
should reuse `postprocess/call_routes.py::resolve_call_route()` — proven twice now (PHP's
Laravel, JS's Express/Fastify) — rather than inventing a third variant.

This final remaining increment should get its own `/plan` pass when picked up (same process
used for Python, PHP, and JS/TS/React).

---

## PHP — done

Built `treesitter-indexer/php/extractor.py` + `php/calls.py` on `tree-sitter-php`, plus the
new `postprocess/call_routes.py::resolve_call_route(calleeName, args)` — the first call-based
(non-annotation) routing resolver in this engine, designed generically from day one per the
approved spec. `php/extractor.py`'s routing call-site recognition (Laravel's `Route::get/post/
put/delete/patch/any/match(...)`, including `Route::middleware('name')->group(closure)` nesting
and both `[Controller::class, 'method']`/`'Controller@method'` handler forms) resolves at
*extraction* time and emits ordinary `HAS_ANNOTATION` edges (synthetic `Get`/`Post`/.../
`Middleware` `AnnotationType` nodes) — so `postprocess/workflow_builder.py`'s new
`build_php_workflows` is a near-direct copy of `build_jvm_workflows` reusing the existing
`resolve_route()` unchanged, and `postprocess/domain_detector.py` needed zero PHP-specific code.

**Real bugs found and fixed via TDD while implementing (worth knowing before touching this code
again):**
- `resolve_call_route` initially scanned the whole argument list for any string instead of only
  checking position 0 for the path — could misattribute a handler reference as a path.
- `Route::any()`/`Route::match()` (explicitly in-scope per the spec) initially returned `None`
  unconditionally — added a local `_ANY_VERB_CALLEE_WORDS = {"any", "all", "match"}` set inside
  `call_routes.py` (deliberately NOT added to `http_routes.py`'s shared `_ROUTE_HINT_WORDS`,
  since "matches any verb" is a different concept from "name looks route-shaped").
- The `[Controller::class, 'method']` handler form's method-name string was never unwrapped
  from its `array_element_initializer` wrapper node — **zero** Laravel array-form routes (or
  `->middleware()->group()`-nested ones) would have been recognized at all without this fix.
- Non-`$this` member calls (`$obj->method()`) returned early instead of falling through to the
  documented 0.50 global-fallback tier — fixed in `_maybe_emit_calls_edge`.
- **A pre-existing, cross-language schema gap surfaced by PHP's `new X()` support**: `Constructor`
  had never appeared on `CALLS`'s TO side at all (only its FROM side) in the shared
  `graph_store.py` schema — `Method`/`Function`/`Constructor` → `Constructor` combos were missing
  entirely, so every `new`-expression CALLS edge from an ordinary method/function silently failed
  to persist (same "unrecognized combo → binder exception → swallowed" failure class as two
  earlier, already-fixed bugs in this project's history). Fixed additively in `graph_store.py`.
- Also added `Package→Function` and `Method/Constructor→Function` CALLS/CONTAINS combos (PHP
  commonly calls free/global helper functions directly, e.g. Laravel's `redirect()`/`response()`/
  `view()`), and extended `build_php_workflows`'s BFS to walk `Function` nodes alongside `Method`
  (mirroring `build_python_workflows`'s multi-table approach, unlike `build_jvm_workflows`'s
  Method-only one) — otherwise those new combos would be schema-supported but never traversed.

**Explicitly deferred, not forgotten:**
- `HttpSuperglobalDetector`-equivalent raw-PHP (`$_GET`/`$_POST`) entry-point detection — a
  different detection mechanism (data-flow into superglobals, not call-site routing), out of
  scope for this increment.
- Constructor-level `$this->middleware('auth')` (only the fluent `Route::middleware(...)
  ->group(...)` form is recognized).
- Fixing the *old* PHP engine's `phpKind` bug — tracked separately below, independent of this
  new-extractor work.

**Fixture**: `e2e-test/fixtures/mini-php-service/` — a Laravel-style app covering constructor
property promotion, class constants, a `new X()` call, a free function, a duck-typed call (0.50
tier), an HTTP-dependency literal, and both route-handler forms including the middleware-group
case. Deliberately uses a static `OrderRepository::findWithBilling()` call (not purely
constructor-injected property access) for one route, specifically so at least one entry point
produces a real (0.90+) multi-hop Workflow trace — property-mediated `$this->repository->
method()` calls are realistic Laravel code but only ever resolve at the 0.50 tier per the
approved scope boundary (no type inference beyond `$this`), so a fixture using *only* that
pattern would produce zero Workflow traces despite everything working as designed.

---

## JS/TS/React — done

Built `treesitter-indexer/js/extractor.py` + `js/calls.py` on two grammars for the first time
in this engine (`tree-sitter-javascript` for `.js`/`.jsx`/`.mjs`/`.cjs`, `tree-sitter-typescript`'s
`language_typescript()`/`language_tsx()` for `.ts`/`.tsx`, selected per file by extension). Both
backend-routing mechanisms (Express/Fastify call-based via `resolve_call_route()`, NestJS
decorator-based via `annotation_http_verb()` directly) convert to ordinary `HAS_ANNOTATION`
edges at extraction time — same pattern as PHP's Laravel routes — so `build_js_workflows` reads
`HAS_ANNOTATION` and calls the existing `resolve_route()` unchanged, needing no query union.
React component/hook detection stayed a pure naming heuristic (PascalCase/`use*`-prefix) as
planned — no JSX-return-type analysis. The pipeline's language-category key for this whole
family is `"js"` (not `"javascript"`), confirmed in `pipeline/codedoc/language_registry.py`.

**Real bugs found and fixed via TDD while implementing (worth knowing before touching this code
again):**
- A class property's name is in tree-sitter's `property` field, not `name` (unlike
  `method_definition`, which does use `name`) — this silently broke the common React
  class-component `handleClick = () => {...}` pattern (every such field got an empty name).
- Inline named function expressions passed directly as a route handler
  (`app.get('/x', function show(req, res) {...})`) were never captured by the top-level scan
  (which only walks declared top-level callables) — the route would never resolve a target at
  all. Fixed by emitting the handler as its own `Function` node on first use, inside the routing
  recognition code itself.
- **Decorators parse with a fundamentally different AST shape between the two grammars**: in
  plain JS, a decorator is nested *inside* `method_definition` as a child; in TS, it's a
  positional sibling in `class_body` before the member it decorates. The decorator-pairing logic
  only handles the TS shape — confirmed empirically this is fine in practice, since NestJS (the
  only decorator-based routing framework in scope) is always written in TypeScript; the fixture
  originally tried a `.js`-file decorator case and silently produced zero entry points until
  moved to a `.ts` file.
- `REFERENCES_CONFIG` (HTTP-dependency detection) and `HAS_PARAMETER`/`RETURNS` all lacked
  `Function`/`ArrowFunction`/`Component`/`Hook` on their FROM side — JS is the first language
  where non-Method/Constructor callables are extremely common (top-level functions, React
  components/hooks), so every one of these silently dropped a real edge until fixed additively.
  Same "unrecognized combo → binder exception → swallowed" failure class as every prior
  increment's schema gaps.
- `scripts/inspect_graph.py`'s entry-points and node-count sections were hardcoded to `Method`
  and a fixed table list respectively — generalized to also check `Function`/`ArrowFunction`
  (entry points) and include `Component`/`Hook`/`ArrowFunction` (node counts), since this is the
  first language where real signal lives on those tables.
- `compare_graphs_js.py`'s own `EXPECTED_NEW_ONLY_*` checks were initially global rather than
  per-fixture — `mini-react-app` (no HTTP routes at all) was falsely flagged for not having
  `Workflow`/`WORKFLOW_STEP` nodes that only `mini-express-service` is expected to produce.

**Explicitly deferred, not forgotten:**
- Next.js file-based API routing (`pages/api/*.js`/`app/api/*/route.js`) — a third routing shape
  (filesystem convention, not call-based or decorator-based) genuinely different from both
  mechanisms this increment built.
- `tsconfig.json` path aliases and `node_modules` resolution for CALLS/imports.
- Full generic-parameter modeling (`Array<User>` only captures the base name `Array`).
- CommonJS `require()`/`module.exports` is NOT modeled as import resolution at all — only ES
  `import`/`export` syntax resolves cross-file (confirmed via real fixture testing: a
  `require()`-based handler reference across files never resolves a target, falling through to
  the 0.50 global tier at best). The `mini-express-service` fixture uses ES modules for exactly
  this reason, despite CommonJS being very common in real Express code.
- The stale `CLAUDE.md` route-map/component-boundaries claim — unrelated documentation fix.

**Fixtures**: two, since no single fixture exercises both gaps closed —
`e2e-test/fixtures/mini-express-service/` (Express call-based routes via named top-level
functions/arrow-functions, a NestJS `@Get()` controller in a `.ts` file, a same-file `this`-free
constructor-target CALLS edge, a free-function import-resolved (0.90) chain producing a real
Workflow trace, and a module-level-instance property-mediated call demonstrating the documented
0.50-tier scope boundary) and `e2e-test/fixtures/mini-react-app/` (a function component + hook
via naming heuristic, `USES_HOOK` vs. `CALLS` distinction, and a class component's
`handleClick = () => { this.increment(); }` pattern — the single most direct regression-proof
of the `this.method()` fix, inside realistic React code rather than a synthetic unit test).

---

## Kotlin

### Background

Kotlin support today lives entirely inside the *old* JVM/Gradle indexer
(`indexer/app/.../KotlinSourceParser.java`, using the real Kotlin compiler PSI — richer than a
lightweight parser). It already models data classes, sealed classes/interfaces, object
declarations, companion objects (`COMPANION_OF`), extension functions (`EXTENSION_OF`),
suspend functions, and property delegation. Conveniently, **all of these target node/rel tables
already exist in `treesitter-indexer/graph_store.py`'s schema but are currently unpopulated**
(confirmed by direct read): `DataClass`, `SealedClass`, `SealedInterface`, `ObjectDecl`,
`CompanionObject`, `ExtensionFunction`, `SuspendFunction`, `Property`, plus rel tables
`SEALED_SUBTYPE`, `DELEGATES_TO`, `COMPANION_OF`, `SUSPENDS`, `EXTENSION_OF` — so this increment
is largely "populate tables that already exist," not new schema design.

Confirmed gaps in the *old* Kotlin parser, beyond the schema-mismatch bug tracked separately
below:
- **No annotation-*usage* extraction at all** — only annotation-*declaration* detection exists
  (`JavaSourceParser.java` has a full `visitAnnotations()` walk; `KotlinSourceParser.java` has
  no equivalent). Consequence: a Kotlin+Spring repo produces **zero** `HAS_ANNOTATION` edges,
  therefore zero HTTP entry points, therefore zero Workflow traces today, independent of the
  CALLS bug. This is the single highest-value new-design item for Kotlin — once fixed, it
  unlocks `resolve_route`/`layer_from_annotation` for Kotlin+Spring with **zero** further
  postprocess/ changes, exactly like Java.
- **Zero Ktor support anywhere** — Ktor's routing DSL (`routing { get("/path") { ... } }`) is
  call-based, the same category as Express/Laravel, and should reuse `resolve_call_route`
  (built during the PHP increment, extended during JS) rather than inventing a third variant.
- Several schema entries are declared but the old parser never populates them even for Kotlin:
  `SEALED_SUBTYPE` (comment in the code says it needs more sophisticated analysis, never
  implemented), `SUSPENDS`, dedicated `Lambda` nodes (lambda bodies are walked but attributed to
  the enclosing function).
- Call resolution is simple-name-only (no receiver-type or overload resolution) — weaker than
  even the old Java engine's.
- Android repos are correctly rejected early (`AndroidManifest.xml` detection) before any of
  this runs — Kotlin support targets non-Android backend/JVM services specifically.

### Goal

Build `treesitter-indexer/kotlin/extractor.py` on `tree-sitter-kotlin`, explicitly **not**
touching or extending the old `KotlinSourceParser.java` (same decoupling philosophy as every
other increment) — using it purely as a structural spec to port from, the same way Java's
tree-sitter extractor was informed by `JavaSourceParser.java`. Two genuinely new pieces of work,
beyond "port the structural model":
1. Annotation-usage extraction (the highest-value item — see above).
2. Ktor's call-based routing DSL, via the shared `resolve_call_route` mechanism.

### Files likely touched

- `treesitter-indexer/kotlin/extractor.py` (new), `kotlin/calls.py` (new)
- `postprocess/workflow_builder.py` — Kotlin's Spring/JAX-RS case should need **zero** new
  postprocess code (reuses `build_jvm_workflows` if Kotlin nodes are typed compatibly with
  Java's, e.g. `Method`/`Class`) or a small parallel function if not; Ktor needs the
  `resolve_call_route` mechanism instead
- New fixture, e.g. `e2e-test/fixtures/mini-kotlin-service/`
- `treesitter-indexer/scripts/compare_graphs_kotlin.py`

---

## Related, separately-fixable bugs found during this research (NOT part of the new-engine work)

Both are small, independent, high-confidence bugs in the *old*, currently-shipping indexers —
good quick wins decoupled from any tree-sitter increment above, in the same spirit as the
already-fixed `HAS_ANNOTATION value STRING` and i18n-resource-bundle bugs found earlier in this
project's history. Deliberately deferred (user decision) during the Python increment's planning
rather than fixed then, so they remain open:

- **Kotlin CALLS edges likely silently fail to persist (100% of them)**: `KotlinSourceParser.java`
  sets a `resolved` boolean property on every CALLS edge it creates, but the shared CALLS rel
  table schema only declares `lineNumber`/`confidence`/`reason` — no `resolved` column. Unlike
  the node-property write path (which filters/allowlists before writing), the relationship-write
  path dumps the full property map straight into the `CREATE` Cypher literal with no filtering;
  an unrecognized property on a KuzuDB relationship CREATE typically fails the whole statement,
  silently caught by a blanket per-relationship try/catch. **Not yet runtime-confirmed** (static
  analysis only) — first step of the fix should be confirming this by running the old Kotlin
  indexer against a real repo and inspecting whether any CALLS edges exist at all. If confirmed,
  the fix is a one-line property rename/alignment, or extending the rel-write path with the same
  filtering the node-write path already has.
- **PHP `phpKind` silently dropped**: `indexer/parsers/php/parse.php` correctly sets
  `phpKind: 'trait'`/`phpKind: 'constant'` in the JSON graph it produces, but the shared
  `KuzuStore.save()` in `indexer/parsers/python/store.py` (reused by the PHP→Kuzu bridge)
  filters node properties through a hardcoded allowlist that doesn't include `phpKind` — silently
  dropped before reaching the database, contradicting CLAUDE.md's documented claim that traits
  are queryable this way. Same failure shape as the historical `HAS_ANNOTATION value STRING` bug.
  Fix is a one-line allowlist addition.
