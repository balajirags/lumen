# Unified old/new indexer switch + safe multi-language merge for treesitter-indexer

## Background

User request: a single flag should switch the whole pipeline between the old per-language
indexer engine (`indexer/`, KuzuDB) and the new tree-sitter engine (`treesitter-indexer/`,
LadybugDB) — and critically, for a polyglot/fullstack repo, the new engine should route each
detected language to its own tree-sitter binary within one run, exactly like the old engine
already does for `cmg-java`/`cmg-js`/`cmg-python`/`cmg-php`. Stated requirement: **"I want a
clean and extensible architecture"** — this should steer the eventual design choice (see
"Proposed approaches" below), not just the quickest patch.

Today there is no single flag. `pipeline/codedoc/cli.py`'s `run` command has five independent,
all-off-by-default flags: `--experimental-treesitter-java/-python/-php/-js/-kotlin`. Each one
is gated by its own `should_use_treesitter_<language>()` function
(`pipeline/codedoc/stages/indexer.py:20-78`), and **every gate requires the repo to be
single-language-category for that language** (`ordered_languages == ["jvm"]`,
`== ["python"]`, etc. — Java/Kotlin additionally guard against each other via
`state.language_flavors` since they share the one `"jvm"` category key in
`pipeline/codedoc/language_registry.py`). If a flag is passed but the repo doesn't match, the
gate silently fails and `run_indexer()` falls back to the legacy binary for that language with
just a log line — no error. So today, any polyglot/fullstack repo **always** uses the old
engine for every language, regardless of how many experimental flags are passed.

### Why this isn't just "add one flag that sets the other five"

A background investigation (prompted by this request) confirmed that naively fanning a single
flag out to run each language's existing `parse_<lang>.py` CLI in sequence against one shared
LadybugDB file — mirroring how the old engine's `run_indexer()` loop already does this for
`cmg-*` binaries — is **not safe today**. Two real, confirmed bugs in
`treesitter-indexer/postprocess/`:

1. **`domain_detector.py`'s `detect_domains()` is not idempotent and not language-scoped.** It
   unconditionally re-queries all `CLUSTERABLE_TABLES` (`Class`/`Interface`/`Enum`/`Method`),
   re-runs label propagation over the *entire current graph*, and unconditionally creates new
   `Domain`/`IN_DOMAIN` nodes/edges — with no check for domains a prior call already created.
   Calling it once per language (as every `parse_<lang>.py` does today, right after its own
   extraction) would re-cluster nodes a previous language's call already domained, inflating
   and duplicating `Domain` nodes.
2. **The `build_<language>_workflows()` functions aren't actually language-filtered.**
   `Method`/`Function`/`Class` are shared node tables across all five languages (distinguished
   only by a `language` property, not separate tables per language), but every
   `build_<language>_workflows()` in `postprocess/workflow_builder.py` queries those shared
   tables with **no `WHERE n.language = '<language>'` clause**. Running two languages' builders
   against one merged DB risks one language's builder processing another language's nodes —
   misattributed or double-counted `Workflow`/`WORKFLOW_STEP` edges.

No existing test or fixture catches this: every `test_*_postprocess.py` file only exercises its
own language's builder in isolation, and every fixture under `e2e-test/fixtures/` is
single-language. The capability this backlog item asks for has simply never been exercised.

**Confirmed safe, by contrast** (so these are not blockers):
- `--clear` defaults to `False` in every `parse_<lang>.py` (Java/Python/PHP/JS/Kotlin) —
  sequential writes into one LadybugDB file won't wipe each other, unlike the old engine's
  `cmg-java`, whose `--incremental` flag defaults to `false` (i.e. **clears by default**,
  confirmed in `indexer/app/.../App.java:69-70,271-274` and `KuzuGraphStore.clear()`'s full
  `DROP TABLE` sweep) — a real, separate, pre-existing asymmetry in the *old* engine worth
  knowing about but out of scope here (see "Related old-engine finding" below).
- `graph_store.py`'s schema (`CREATE ... TABLE IF NOT EXISTS`, ~32 statements) is genuinely
  idempotent — calling `init_schema()` repeatedly across multiple language invocations against
  the same file is safe.

### The real architectural difference from the old engine

`App.java` (old engine) merges every language into **one in-memory `Graph` object first**,
runs `WorkflowBuilder`/`DomainDetector` **exactly once** on that merged graph, then saves
**once**. The new engine's `parse_<lang>.py` scripts each do extract → postprocess → write, all
three, independently, per language — correct for every fixture so far because none of them was
ever polyglot. A single unified flag that's actually safe on fullstack repos requires closing
this gap, not just wiring a flag to the existing per-language scripts.

## Proposed approaches

### A — Patch the current per-binary CLI architecture

Add a `--skip-postprocess` flag to each `parse_<lang>.py`. The pipeline's dispatch loop writes
every detected language's extraction-only output into the shared LadybugDB first, then:
- fixes the two confirmed bugs: add `WHERE n.language = '<language>'` to every
  `build_<language>_workflows()` query (additive, mechanical, same pattern as every other
  language-scoping fix already made in this codebase), and move `detect_domains()` to run
  **once**, after all languages have written, instead of once per language (its current
  behavior — unconditional full-graph re-cluster — is actually *correct* once it only ever runs
  a single time per run).
- Smaller diff, keeps the existing per-language subprocess shape (each language's binary stays
  independently testable/debuggable exactly as today).
- Downside: still two passes conceptually bolted together (write-only CLI mode + a separate
  "run postprocess now" step) rather than one coherent flow: a patch, not a redesign.

### B — True in-memory merge, mirroring `App.java` exactly

A new orchestrator calls each detected language's extractor directly (reusing the in-memory
`GraphModel` each `<language>/extractor.py` already builds before handing it to
`graph_store.py` — confirmed this object exists today, per-language, before any write happens),
merges all languages' graphs into one `GraphModel`, runs each language's `build_<language>_
workflows()` once plus `detect_domains()` once on the merged result, then writes **once**.
- Architecturally the cleanest option, and the one that actually matches "clean and
  extensible": one orchestration entry point, one merge step, one postprocess pass, one write —
  structurally identical to how the old engine already solved this exact problem.
- Bigger: needs a `GraphModel.merge()` (or equivalent) that doesn't exist yet, and likely a
  small refactor so each `<language>/extractor.py` exposes a reusable "extract to GraphModel"
  entry point decoupled from its own CLI/store-write flow (today each `parse_<lang>.py` likely
  calls extraction and writing together in one `main()` — needs confirming per language during
  planning).
- Sets the shape every future language increment should follow from here on, rather than
  leaving two slightly different code paths (single-language direct-write vs. multi-language
  deferred-write) to maintain in parallel.

**User's stated preference ("clean and extensible architecture") leans toward B**, but no
final decision has been made yet — this is an open choice for whoever picks this up to confirm
with the user before writing a design spec, not a locked-in answer.

## Open questions for whoever implements this

- **Approach**: A vs. B (see above) — needs explicit user sign-off before a `/plan` is written;
  given the stated "clean and extensible" goal, lead with B as the recommendation but let the
  user weigh the bigger lift.
- **Flag design**: should the new unified flag (e.g. `--experimental-treesitter`) *replace* the
  five existing per-language flags, or sit *alongside* them (kept for isolating a single
  language during testing/debugging, with the umbrella flag as shorthand for "all languages
  present")? Not yet decided with the user.
- **Naming**: `--experimental-treesitter` (singular, no per-language suffix) is the obvious
  candidate but unconfirmed.
- Should the single-language-category restriction in each `should_use_treesitter_<language>()`
  gate simply be deleted once merge-safety is fixed, or replaced with a narrower check (e.g.
  still excluding Java+Kotlin mixed repos specifically, since `kotlin/extractor.py` only
  understands Kotlin syntax and vice versa for `java/extractor.py`)?
- Whichever approach is chosen, Kotlin's existing `"jvm"`-category-sharing-with-Java quirk
  (`should_use_treesitter_kotlin` / `should_use_treesitter_java`, mutually exclusive via
  `state.language_flavors`) needs to keep working — a fullstack repo with both Java and Kotlin
  files mixed in the same JVM slice still has no tree-sitter path today and this backlog item
  doesn't change that; confirm it stays an explicit, documented fallback-to-`cmg-java` case
  rather than a silent gap.

## Rough task breakdown (approach-agnostic until A/B is decided)

- [ ] Get explicit user decision: approach A or B, and flag-replace-vs-alongside
- [ ] `/plan` the chosen approach (this is architectural-path work per the repo's brainstorming
      convention — needs a design spec in `docs/superpowers/specs/`, not a direct patch)
- [ ] Fix `build_<language>_workflows()` language-scoping (needed by both approaches)
- [ ] Fix `detect_domains()` to run exactly once per overall run, not once per language (needed
      by both approaches)
- [ ] Implement the chosen approach's orchestration (deferred-postprocess CLI flags for A, or a
      merge-capable orchestrator + `GraphModel.merge()` for B)
- [ ] Build a new polyglot fixture (e.g. `mini-fullstack-service/` combining at least two
      languages) — the first one in this engine's history; none exists today
- [ ] New `compare_graphs_fullstack.py`-equivalent or extend an existing one to assert
      per-language Domain/Workflow counts are correct and non-duplicated post-merge
- [ ] Wire the new flag(s) into `cli.py` and `should_use_treesitter_*` (or their replacement)
- [ ] Update `CLAUDE.md`/`README.md` once shipped (standing rule — see memory)

## Related old-engine finding (separate, not part of this item's scope)

While investigating this, a second, independent correctness gap was found **in the old
engine**: `cmg-java`'s `--incremental` flag defaults to `false`, meaning it **clears the entire
DB** (`KuzuGraphStore.clear()` drops every node/rel table) unless `--incremental` is explicitly
passed — but `pipeline/codedoc/stages/indexer.py`'s `run_indexer()` never passes it. Since
`ordered_languages` is sorted by file-count descending (not a fixed language-priority order),
a polyglot repo where a non-JVM language has *more* files than the JVM slice would run that
language's binary first (writing successfully), then run `cmg-java` second, which would
silently wipe out everything the first binary just wrote. `e2e-test/test-lumen.sh`'s
`FIXTURES` array only contains `mini-flask-app` (single-language), so this has likely never
been exercised by any test. Worth its own backlog item if confirmed — not fixed here, and not
blocking this item, since it affects the *old* engine's existing multi-language claim, not the
new one.

## Files likely touched

- `pipeline/codedoc/cli.py`, `pipeline/codedoc/config.py`, `pipeline/codedoc/state.py` — flag(s)
- `pipeline/codedoc/stages/indexer.py` — `should_use_treesitter_*` gates / dispatch loop
- `treesitter-indexer/postprocess/domain_detector.py` — single-pass-per-run fix
- `treesitter-indexer/postprocess/workflow_builder.py` — `n.language` filter per builder
- `treesitter-indexer/graph_model.py` — merge support, if approach B is chosen
- `treesitter-indexer/parse_{java,python,php,js,kotlin}.py` — `--skip-postprocess`, if approach
  A is chosen
- New fixture under `e2e-test/fixtures/`
- `CLAUDE.md`, `README.md`
