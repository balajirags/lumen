# Tree-sitter indexer: remaining languages (PHP, JS/TS/React, Kotlin)

## How to resume this work (read this first)

Everything below is written to be self-contained — you shouldn't need to re-read old
conversation history to pick this back up. Start here:

0. **Current uncommitted state** (as of this writing, `git status --short` on
   `feature/treesitter-java-indexer` shows): this file itself is untracked and not yet
   committed — commit it (with a one-line message per the convention below) once you've read
   it, so it isn't lost. `backlog/incremental-reindexing.md` and
   `backlog/multi-repo-cross-service-graph.md` are also untracked but **unrelated** to this
   indexer work (pre-existing scratch files from earlier work on this branch) — don't assume
   they belong together or bundle them into the same commit.
   `indexer/parsers/python/__pycache__/store.cpython-311.pyc` shows modified but is a stray
   bytecode-cache diff from running the *old* Python indexer's tests, also unrelated — fine to
   leave alone or `git checkout` it away, don't include it in any indexer-work commit.
1. `git checkout feature/treesitter-java-indexer` (all of this work — Java, Python, and every
   future language increment — lives on this one branch; the name is stale now that it covers
   more than Java, not worth renaming mid-flight).
2. `git log --oneline -15` to see what's landed. As of this writing, the newest relevant commits
   are `85875e1` (Python increment) and the several `Generalize .../Detect .../Extend ...`
   commits below it (Java increment's later generalization work — HTTP dependencies, workflow
   entry-points, layer labeling — all framework-agnostic, see "Conventions" below).
3. Sanity-check the existing engine still works before adding anything new:
   ```bash
   cd treesitter-indexer
   .venv/bin/python3 -m unittest discover -s tests   # expect ~57 passed, 0 failed
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

**Recommended remaining order: PHP → JS/TS/React → Kotlin** (deviates from a literal
python/js/typescript/react/php/kotlin listing — confirmed with the user during the Python
increment's planning). Full reasoning is in the plan history, condensed here:

- **PHP next**: smallest, best-scoped remaining gap (mostly `Parameter`/`RETURNS` extraction,
  which is entirely missing today) — but more importantly, Laravel's `Route::get($path,
  $handler)` is a **call-based** routing DSL, the same shape as Express/Fastify/NestJS routing
  and Ktor's `routing { get(path) { ... } }`. `postprocess/http_routes.py`'s own docstring
  already flags this as out of scope for the annotation-tokenizing approach. Building a
  generalized `resolve_call_route(calleeName, args)` resolver here — on PHP's small, familiar
  surface, with a working (non-generalized) reference implementation already in the old
  `RouteExtractor` to port from — means JS/TS/React and Kotlin both inherit a proven mechanism
  instead of inventing one under a much bigger increment's time pressure.
- **JS/TS/React after that**: highest scope and risk of the three (real TS type extraction,
  an open React-detection design question, multiple confirmed CALLS bugs, zero existing
  fixtures) — best tackled once the confidence-tier CALLS recipe and the call-route resolver
  are both mature.
- **Kotlin last**: lowest new-design risk for its dominant case (Spring/JAX-RS-annotated JVM
  backends — near-exact repeat of Java's `HAS_ANNOTATION`/`resolve_route` pattern), but the
  largest raw grammar surface (data classes, sealed types, coroutines, delegation), and a
  narrower deployment surface (non-Android JVM backends only) than Python/JS/PHP's near-universal
  presence.

Each language increment should get its own `/plan` pass when its turn comes (mirroring how
Python's plan was written) rather than trying to plan all three now — the full plan history
for Python (including the detailed per-language research this file summarizes) is worth
re-reading first for context; ask where it's archived if not obviously discoverable.

---

## PHP

### Background

The old `indexer/parsers/php/parse.php` (`nikic/php-parser`-based) already does a reasonable
job: classes/interfaces/traits (`phpKind: 'trait'`), methods/constructors, typed properties
(PHP 7.4+ only), class constants, and — via `RouteExtractor` — Laravel route detection that
maps HTTP verbs onto the same annotation names Java/Spring uses (`GetMapping` etc.), so
`WorkflowBuilder`'s JVM-shaped `HTTP_ANNOTATIONS` check already works for PHP too, by
hardcoded-name reuse rather than genuine generalization.

Confirmed gaps (verified by direct code read during this session's research):
- **No parameter/return-type extraction at all** — `HAS_PARAMETER`/`RETURNS` are never emitted
  for PHP. Straightforward to close from PHP 7.4+ type hints, same shape as the Python increment.
- **`Route::middleware(...)->group(closure)` unhandled** — routes inside the closure are still
  found (generic AST recursion), but carry no middleware/auth signal. Already scoped in
  `backlog/php-laravel-middleware-detection.md` (that backlog targets the *old* PHP parser;
  the new tree-sitter PHP extractor should solve this properly from the start instead of
  porting the old gap forward).
- **`HttpSuperglobalDetector` (raw `$_GET`/`$_POST`/`$_REQUEST` entry-point detection) is fully
  implemented in the old parser but never instantiated** — dead code. Today, PHP entry-point
  detection is 100% Laravel-route-based; raw-PHP apps get nothing. Worth deciding whether the
  new extractor should support this case or explicitly scope it out.
- **A newly-discovered old-engine bug** (`phpKind` silently dropped by the shared `KuzuStore`
  write-allowlist) is tracked separately below under "Old-engine bugs" — it's a quick,
  independent fix to the *existing* shipping engine, not part of this new-extractor work.

### Goal

Build `treesitter-indexer/php/extractor.py` on `tree-sitter-php`, reaching rough parity with
the old engine's structural extraction plus the two closed gaps above, and — the one genuinely
new piece of design work — a language-agnostic `resolve_call_route(calleeName, args)` sibling
to `resolve_route()`, tokenizing the callee's method-name segment for an HTTP verb the same way
`annotation_http_verb()` tokenizes annotation names. Built correctly, this should naturally
handle `Route::middleware(...)->group(closure)` nesting and `[Controller::class, 'method']`/
`'Controller@method'` handler forms as a side effect, not a follow-up patch.

### Design questions to resolve during that increment's `/plan`

- Where does `resolve_call_route` live — a new `postprocess/call_routes.py`, or folded into
  `http_routes.py`? It needs to inspect call arguments (path string, handler reference), which
  `resolve_route()` never does today (it only ever sees annotation name/value pairs).
- Does `resolve_call_route` get built PHP-specific first and generalized later, or designed
  generically from day one (recommended, since JS and Kotlin both need the identical shape
  next) — i.e. is the call-site AST shape (`callee.method`, positional args) something that can
  be captured once during extraction into a language-agnostic intermediate form?
- Scope boundary for `HttpSuperglobalDetector`-equivalent raw-PHP detection: build it now (the
  spec already exists in the old parser) or explicitly defer?

### Files likely touched

- `treesitter-indexer/php/extractor.py` (new), `php/calls.py` (new, PHP's existing two-tier
  CALLS scheme — `$this->`/`self::`/`static::` at 0.95, static-class/free-function/`new` at
  0.90 — should gain the same 0.50 global-fallback tier Python's increment added)
- `treesitter-indexer/postprocess/http_routes.py` or a new `postprocess/call_routes.py`
- `treesitter-indexer/graph_store.py` (only if `resolve_call_route` needs new persisted fields —
  likely not, if call-site data is captured into the same annotation-shaped `{name: value}` map)
- New fixture, e.g. `e2e-test/fixtures/mini-php-service/` (none exist today for the new engine)
- `treesitter-indexer/scripts/compare_graphs_php.py` (sibling to `compare_graphs_python.py`)

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
