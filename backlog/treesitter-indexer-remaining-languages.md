# Tree-sitter indexer: remaining languages (JS/TS/React, Kotlin)

## How to resume this work (read this first)

Everything below is written to be self-contained — you shouldn't need to re-read old
conversation history to pick this back up. Start here:

0. **Current uncommitted state**: as of the PHP increment's completion, everything relevant is
   committed. `backlog/incremental-reindexing.md` and `backlog/multi-repo-cross-service-graph.md`
   remain untracked but **unrelated** to this indexer work (pre-existing scratch files from
   earlier work on this branch) — don't assume they belong together.
   `indexer/parsers/python/__pycache__/store.cpython-311.pyc` may show modified (stray bytecode
   cache from running the *old* Python indexer's tests) — unrelated, leave alone.
1. `git checkout feature/treesitter-java-indexer` (all of this work — Java, Python, PHP, and
   every future language increment — lives on this one branch; the name is stale now that it
   covers more than Java, not worth renaming mid-flight).
2. `git log --oneline -20` to see what's landed. As of this writing, the newest relevant commit
   is `2063a65` (PHP pipeline wiring); the PHP increment spans roughly `baba1a5`..`2063a65`, with
   `85875e1` being the earlier Python increment and `0fc208a`/the file at
   `docs/superpowers/plans/2026-10-09-php-tree-sitter-indexer.md` being PHP's design+plan docs
   (good reference for how much detail to put in the next language's plan).
3. Sanity-check the existing engine still works before adding anything new:
   ```bash
   cd treesitter-indexer
   .venv/bin/python3 -m unittest discover -s tests   # expect 89 passed, 0 failed
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

**Recommended remaining order: JS/TS/React → Kotlin** (PHP, done above, was deliberately
sequenced ahead of both — see rationale below, now validated: the generalized
`resolve_call_route(calleeName, args)` resolver it built is ready for both to reuse as-is).

- **JS/TS/React next**: highest scope and risk of the two remaining (real TS type extraction,
  an open React-detection design question, multiple confirmed CALLS bugs, zero existing
  fixtures) — best tackled once the confidence-tier CALLS recipe and the call-route resolver
  are both mature.
- **Kotlin last**: lowest new-design risk for its dominant case (Spring/JAX-RS-annotated JVM
  backends — near-exact repeat of Java's `HAS_ANNOTATION`/`resolve_route` pattern), but the
  largest raw grammar surface (data classes, sealed types, coroutines, delegation), and a
  narrower deployment surface (non-Android JVM backends only) than Python/JS/PHP's near-universal
  presence.

Each remaining language increment should get its own `/plan` pass when its turn comes (same
process used for Python and PHP) rather than trying to plan both now.

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

## JS/TS/React

### Background

The old `indexer/parsers/javascript/parse.js` (Babel-based, ~1090 lines) has several confirmed,
concrete bugs beyond mere feature gaps:
- **React "component-ness" is pure naming convention** (PascalCase regex for components,
  `/^use[A-Z]/` for hooks) — no check that a "component" actually returns JSX.
- **CALLS has only 2 of the usual 3 tiers, with no 0.50 fallback at all** — edges are *dropped*,
  not downgraded, when they don't match. Relative-import resolution only (no `node_modules`,
  no `tsconfig.json` path aliases, no directory `index.js` resolution).
- **`this.method()` inside a class never produces a resolvable CALLS edge** — the code builds
  a wrong target id that never matches an existing node, so the edge is silently dropped by a
  final id-existence filter. Intra-class method calls are structurally invisible in the current
  JS call graph. This is likely the single highest-value CALLS fix of the three languages.
- **Schema/emission mismatches silently drop edges**: several shapes the parser tries to create
  (`ArrowFunction->ArrowFunction`, `Component->Component`, `Hook->ArrowFunction`) aren't declared
  combos in the `CALLS`/`USES_HOOK` rel tables — the resulting KuzuDB binder exceptions are
  unconditionally caught-and-discarded by `store.js`, with zero visibility into how much data
  is lost this way.
- **Zero backend framework detection** (Express/NestJS/Fastify/Next.js API routes) — see
  `backlog/js-backend-entrypoint-detection.md` (already-scoped, unimplemented). A separate,
  purely textual/regex signal in `pipeline/codedoc/repo_classification.py` feeds repo-type
  classification only — it never reaches the graph.
- **TypeScript**: class property type annotations work; interface *method* signatures
  (`TSMethodSignature`) are silently skipped; the `isInterface: true` flag is dropped by
  `store.js`'s property write-allowlist, so classes and interfaces are indistinguishable in the
  persisted graph; no type-alias or generics extraction at all.
- **Zero existing JS/TS/React fixtures or tests anywhere in the repo** — this increment starts
  its validation harness from nothing, unlike PHP/Python which can extend existing precedent.
- (Documentation note, not an indexer bug, but worth fixing while in this area: `CLAUDE.md`
  currently states route-map/component-boundaries artifacts are conditionally "suppressed for
  JS-frontend repos" — confirmed stale; they were later removed from `artifact_planner.py`'s
  packs entirely, for every repo type. Two existing tests fail today asserting the old,
  superseded conditional-suppression behavior:
  `test_fullstack_route_map_becomes_conditional_with_weak_frontend_signal` and
  `test_fullstack_route_map_stays_required_with_strong_frontend_signal`. Unrelated to the new
  indexer engine — a separate, small, independent fix worth doing whenever convenient.)

### Goal

Build the new extractor on `tree-sitter-javascript` + `tree-sitter-typescript` (two grammars,
selected by file extension — a first for this project). Bundles four largely independent
problems, each needing its own explicit design decision:

1. Fix the `this.method()` id-construction bug and add the missing 0.50 CALLS tier, applying
   the same same-corpus type-index recipe used for Java/Python.
2. Fix the schema/emission mismatches by aligning emitted shapes with declared table combos
   from the start — no silent-catch-and-discard inherited from the old engine.
3. Real TypeScript structure: interface method signatures, a real `isInterface`/`isClass`
   distinction that survives persistence, type aliases, generics — none of which have a
   Python/PHP/Java analog to reuse.
4. An explicit decision on React component detection: continue the PascalCase/`use*` naming
   heuristic (cheap, consistent with the engine's syntactic-not-semantic philosophy elsewhere)
   vs. real JSX-return-type detection (more accurate, first semantic-analysis-flavored
   departure from that philosophy). Recommendation from this session's research: start with the
   heuristic for parity and revisit only if fixture testing shows a real accuracy problem.
5. Apply the `resolve_call_route` mechanism built during the PHP increment to
   Express/Fastify/NestJS/Next.js routing — extending it for NestJS's decorator-based routes
   (`@Get()`) via the `HAS_ANNOTATION` mechanism once JS decorators are wired into it, rather
   than inventing a JS-specific decorator mechanism from scratch.

### Files likely touched

- `treesitter-indexer/javascript/extractor.py` (new) — or `js/`, naming TBD when this increment
  is planned; needs a strategy for TS-vs-JS grammar selection per file
- `javascript/calls.py` (new)
- `postprocess/workflow_builder.py` — new `build_js_workflows` (parallel to `build_jvm_workflows`
  and `build_python_workflows`, not a modification of either)
- `postprocess/http_routes.py` / wherever `resolve_call_route` ends up living after PHP
- New fixtures under `e2e-test/fixtures/` — first ones for this language family
- `treesitter-indexer/scripts/compare_graphs_js.py`

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
