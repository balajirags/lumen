# Unified `--experimental-treesitter` switch with in-memory multi-language merge — design

## Context

`treesitter-indexer/` covers Java, Python, PHP, JS/TS/React, and Kotlin, but each language was
reachable only through its own `--experimental-treesitter-<language>` flag, and every flag was
gated to a single-language-category repo. Any polyglot or fullstack repo therefore silently fell
back to the old KuzuDB engine for every language, no matter how many flags were passed.

The goal is one flag that routes every detected language through the tree-sitter engine in a
single run, with a clean, extensible architecture. Background, the two confirmed merge-safety
bugs, and the original A/B comparison are in `backlog/treesitter-indexer-unified-switch.md`; this
doc records the decisions taken.

Decisions confirmed with the user: approach **B** (in-memory merge, mirroring the old engine's
`App.java`), and the unified flag **replaces** the five per-language flags.

## Scope

In scope:
- Multi-language orchestrator that extracts every requested language, merges the in-memory
  graphs, saves once, then post-processes once.
- Language-scoping of the workflow builders so languages sharing node tables don't see each
  other's nodes.
- One CLI flag, `--experimental-treesitter`, on `lumen run`; removal of the five per-language
  gates and flags.
- A polyglot fixture and tests.

Out of scope:
- Cross-language CALLS edges (no extractor emits them; Workflows stay per language).
- Fixing the Python extractor's subdirectory import resolution
  (`backlog/treesitter-python-subdirectory-imports.md`).
- The old engine's `cmg-java` clear-by-default behaviour
  (`backlog/old-engine-cmg-java-clears-db.md`).
- Giving `security-audit` the flag (it never had the per-language ones either).

## Findings that shaped the design

- Each language already builds an in-memory `Graph` via `<lang>/extractor.py::extract_repo()`
  before writing, so the extraction half is reusable as-is. Java and Python also call
  `extract_config(repo, graph)`.
- Post-processing (`build_<lang>_workflows(store)`, `detect_domains(store)`) queries the
  **database**, not the model, so it has to run after `save()`.
- `LadybugGraphStore.save()` keeps a per-call node-id map. Saving languages one after another
  into one DB can never create a cross-language edge; one merged save can.
- `save(language=)` is only a default. Java nodes carry no `language` of their own and relied on
  it, so a merged save would mis-tag them unless nodes are stamped before merging.
- `detect_domains()` re-clusters the whole graph on every call. It is already multi-language
  aware (the domain language is derived from its members), so it is correct when run once on a
  merged DB and wrong when run once per language.
- `Method`/`Function`/etc. are shared tables across languages, and the `build_*_workflows`
  queries had no `language` filter.
- Node ids carry no language prefix, so ids can collide across languages.

## Design

### Orchestrator (`treesitter-indexer/parse_all.py`, installed as `bin/cmg-ts`)

For each language in `--languages`: extract, run config extraction once (first language that
uses it), stamp `language` on every node and relationship that lacks one, merge into one
`Graph`. Then `init_schema()` (or `clear()` with `--clear`), one `save()`, each language's
workflow builder once, `detect_domains()` once. `--clear` stays opt-in, the same safe default as
every `parse_<lang>.py`.

### Language registry (`treesitter-indexer/languages.py`)

One `Language` entry per language: extractor, workflow builder, and whether it uses config
extraction. Imports are lazy so a Python-only repo does not import the Kotlin/PHP/JS grammars.
Adding a language is one registry entry plus its extractor and builder.

### Merge semantics (`graph_model.Graph`)

- `stamp_language(lang)` fills `language` only where it is missing.
- `merge(other)` is first-writer-wins. Placeholders every language synthesizes for the same
  real-world entity (`class:external:*`, `package:*`, `annotationtype:*`) dedupe silently on
  purpose. Any other id collision between different languages is reported by returning the ids,
  and `parse_all.py` logs a warning. It never fails the run and never silently attaches one
  language's edges to another language's node.

### Workflow builders

Every builder loads only its own language's callables (`WHERE n.language = '<lang>'`) through a
shared `_load_callables()`, and restricts entry points to that set. The BFS already skips any
callee absent from `node_info`, so this scopes the whole traversal. Single-language runs are
unchanged because their nodes carry the matching language.

### Pipeline

`treesitter_languages(ordered_languages, language_flavors)` maps the pipeline's categories to the
engine's keys. The `"jvm"` category yields `java` and/or `kotlin` from `language_flavors`, with
Java as the default when neither is recorded. When the flag is set, `run_indexer` builds one
`cmg-ts` job covering every language; otherwise it builds one legacy job per language, exactly as
before. Both paths share one execution loop, and the progress panel updates every language row of
a multi-language job.

A mixed Java+Kotlin repo, previously a hard fallback to `cmg-java`, now indexes together: each
extractor only reads its own file extensions, and the builders are language-scoped.

## Testing

- `tests/test_graph_merge.py`: stamping, disjoint merge, placeholder dedupe, real collision
  reported first-wins, same-language duplicate is not a collision.
- `tests/test_polyglot_postprocess.py`: runs the real `mini-fullstack-service` fixture through
  the orchestrator's steps and asserts language tags survive, workflow steps share their
  workflow's language, workflow totals equal the per-builder counts, domain ids are unique, and
  domains exist per language.
- `pipeline/tests/test_indexer.py`: `treesitter_languages` table, plus mocked-subprocess tests
  that the flag produces exactly one `cmg-ts` call, the legacy path produces one call per
  language, and a missing `cmg-ts` gives the install hint.
- Manual: `run_indexer` against the fixture with the real binary, read back through
  `create_graph_backend()`; and a combined Java+Kotlin tree.
- The fixture `e2e-test/fixtures/mini-fullstack-service` is built from copies of existing
  fixtures (Spring, Python service, React app), never a mutation of them. The Python files sit at
  the repo root because of the subdirectory limitation noted above.

## Risks and known limitations

- No cross-language CALLS edges, so a frontend call into a backend endpoint is not linked.
- Removing the five flags is a breaking CLI change, acceptable because the engine is
  experimental and off by default.
- A genuine cross-language id collision keeps the first writer. None occur in the fixtures; the
  warning exists so a real one is visible.
