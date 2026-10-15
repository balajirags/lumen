# Incremental Re-Indexing

## Background

User request: after indexing a repo once, when the repo gets new commits, re-index only what
changed instead of paying for a full from-scratch rebuild every time.

Confirmed by reading the code (not guessed): today `lumen run` is **always** a full fresh build.
`create_run_dir()` (`pipeline/codedoc/pipelines/common.py:12-17`) always makes a brand-new
timestamped output dir, and `full.py:46` calls it unconditionally — there is no concept anywhere
of pointing a new run at a prior run's output dir or DB. `stages/indexer.py:91-92` creates a
fresh `index.kuzu/` inside that new dir and shells out to the language binaries
(`indexer.py:108-117`) without ever passing anything incremental-related.

There is already a **partial, Java-only, broken** attempt at this, worth reusing/fixing rather
than starting from zero:

- `App.java:69-70` has an `--incremental` flag ("Only re-parse files that have changed since the
  last run"). When set, it builds a `FileHashCache` (SHA-256 per file, cached at
  `<repo>/.code-mem-graph-hashes`) and skips `store.clear()` (`App.java:271-274`).
- The hash cache is only wired into `JavaSourceParser`/`CpgParser`
  (`JavaSourceParser.java:108`: skip file if hash unchanged). `SourceParserFactory` passes the
  cache to `JavaSourceParser` only — `JavaScriptSourceParser`/`PythonSourceParser`/
  `PhpSourceParser` ignore it, and `PythonSourceParser.supportsIncremental()` explicitly returns
  `false`.
- **The real gap is the storage layer.** `KuzuGraphStore.save()` inserts nodes with a plain
  `CREATE` against tables with `PRIMARY KEY(id)`, no `MERGE`/upsert, no delete-by-file step, and
  swallows exceptions with the comment *"// Node insert may fail if duplicate or schema
  mismatch"* (`KuzuGraphStore.java:190-191`). So running `--incremental` a second time against an
  already-populated DB: unchanged files are correctly skipped, but a **changed file's new node
  data silently fails to insert** (duplicate PK) while the stale old version stays in the DB —
  the same silent-data-loss failure mode already documented once in this codebase for
  `HAS_ANNOTATION` (see `CLAUDE.md`'s design-decisions table). `store.clear()` itself is a hard
  `DROP TABLE` + rebuild — the only "clear" semantics that exist anywhere are full wipe.
  Interestingly, `Neo4jStore.save()` in the *same file* (`store.py:307`) already does this
  correctly: `UNWIND $batch AS row MERGE (n:{label} {id: row.id}) SET n += row` — that's the
  pattern to copy for the Kuzu path.
- Only one test exercises any of this (`AppTest.java:411-450`), and it only checks the
  parser-level hash-skip in isolation — it never runs `store.save()` against a pre-populated DB,
  so the duplicate-PK problem isn't caught by anything today.
- The pipeline layer never passes `--incremental` to any indexer binary at all
  (`indexer.py:108-117`) — this scaffold is disconnected from `lumen run` entirely.

Two structural constraints any real design has to respect:

- **Cross-file resolution is two-pass and global.** Java, Python, and JS parsers all build a
  first-pass global symbol table (internal type names / internal modules / import maps) by
  scanning *every* file, then resolve calls/imports against it in a second pass. Python
  (`parse.py:801-807`) explicitly **drops** any `CALLS`/`IMPORTS` edge whose target isn't in the
  current run's node set. A naive "only look at changed files" re-parse would silently lose
  cross-file edges into/out of unchanged files unless the full current-repo symbol table is
  rebuilt (cheap) before the expensive per-file parse (skippable for unchanged files) runs.
- **`WorkflowBuilder`/`DomainDetector` are inherently global, not scopable.** `DomainDetector`
  runs label-propagation clustering over the entire graph's adjacency (10 iterations, all
  production nodes); `WorkflowBuilder` does BFS from all entry points over the merged graph.
  Both must simply rerun in full after any incremental update — there's no sound way to scope
  them to "just the changed files," but they're also comparatively cheap (in-memory graph
  algorithms, no AST/file I/O), so this isn't a real cost problem.
- No git usage exists anywhere in the codebase today (`grep` for `git diff`/`git log`/
  `subprocess.*git` across `indexer/` and `pipeline/` returns nothing) — change detection would
  be new infrastructure, not an extension of something existing.
- No prior backlog item mentions "incremental" — this is genuinely new, on top of an abandoned
  partial prototype rather than a documented design.

## Decisions already made with the user

- **Change detection**: git-diff based — compare the last-indexed commit SHA (recorded per run)
  against `HEAD` via `git diff --name-status`, rather than content-hashing every file. Cheap,
  exact, and matches how these repos are actually versioned.
- **Language scope**: all four languages (Java, JS, Python, PHP) from the start, not a
  Java-only first slice — a Java-only incremental pipeline would still force full re-parses of
  JS/Python/PHP files in a polyglot repo, which defeats much of the point.

## Proposed approach

### 1. Track the last-indexed commit per run

Incremental mode needs to point at an *existing* prior run's `index.kuzu` dir instead of
`create_run_dir()`'s always-fresh dir. Record the indexed commit SHA in a small JSON sidecar next
to the DB (e.g. `index.kuzu/<repo>-db/.last-index-meta.json`) written on successful completion —
simplest option, no schema change needed. A new CLI entry point (e.g. `lumen run <repo>
--incremental --from <prev-output-dir>`) reuses that dir/DB rather than creating a new one.

### 2. Compute the file diff via git

`git diff --name-status <last-sha> HEAD` categorized into added/modified/deleted paths, filtered
through the same ignore/extension rules the full parse already uses, so the diff only contains
files the indexer would parse anyway.

### 3. Fix the storage layer: real upsert + delete-by-file (the actual correctness fix)

This is required regardless of language and is the same root-cause class of bug as the
`HAS_ANNOTATION` incident:

- Add a delete-by-file step before re-inserting a changed file's nodes: nodes already carry a
  `path STRING` property, so `MATCH (n {path: $path}) DETACH DELETE n` per changed/deleted file
  works without a schema change, in every store (`KuzuGraphStore.java`, `store.py`, `store.js`,
  and the PHP bridge via `kuzu_writer.py`).
- Replace the Kuzu path's raw `CREATE` for nodes with `MERGE ... SET n += row` (already proven
  correct and present in the same files' `Neo4jStore.save()` implementation — copy the pattern).
  This makes re-runs idempotent so a retried/partial incremental run can never hit a silent
  duplicate-PK-swallowed-exception again.
- Relationship writes should move to the same `MERGE` pattern — delete-by-file removes most
  stale edges as a side effect of deleting the node, but re-running the same changed file twice
  (e.g. a retried run) with raw `CREATE` for edges between two surviving unchanged nodes would
  otherwise produce duplicate parallel edges.

### 4. Cross-file resolution: keep the first pass global, only skip the expensive second pass

The first-pass symbol-table walk (internal type names / modules / imports) must still scan the
*entire* current working tree every time — it's cheap (no CPG/statement/CFG construction) and
guarantees the global table reflects reality, including files that didn't change. The
hash-skip (extend the existing Java `FileHashCache` pattern to JS/Python/PHP, since today it's
Java-only) should only gate the second, expensive pass (full parse, CPG, statements). Deleted
files fall out naturally since the tree walk uses the current working tree, not a stale list.

### 5. Post-processing always reruns in full

After storage is updated for the changed set, rerun `WorkflowBuilder` and `DomainDetector`
unconditionally over the full resulting graph — no scoping attempted, since both are cheap
relative to source parsing and neither can be soundly scoped anyway.

### 6. Pipeline wiring

- `pipelines/common.py` needs an incremental-aware path that reuses an existing output dir/DB
  instead of `create_run_dir()`'s fresh-dir default — the main structural deviation from today's
  pipeline shape.
- `stages/indexer.py`'s `cmd` construction gains `--incremental` + a changed-files manifest
  (a file listing paths, not a long argv list) passed to each language binary, alongside
  `--db-path` pointed at the *prior* run's `index.kuzu` dir.
- After a successful run, rewrite the `.last-index-meta.json` sidecar with the new HEAD SHA.

## Open follow-up questions for whoever implements this

- Should uncommitted working-tree changes (`git status`) also be picked up, or is incremental
  mode strictly "since the last indexed commit" (committed changes only)?
- How to handle a renamed file (git reports rename + similarity score) — treat as delete+add, or
  track it as a path update to avoid unnecessary node churn/id changes?
- What if history was rewritten/rebased since the last index (stored SHA is no longer an
  ancestor of `HEAD`)? Needs a clear fallback to a full re-index, not a crash.

## Verification

1. Index a fixture repo fully; make a small, deliberate change set (one file added, one
   modified, one deleted) plus a new commit; run incremental re-index; confirm: unchanged files'
   node `id`s are untouched (not recreated), the deleted file's nodes are gone, the modified
   file's nodes reflect new content, and cross-file edges into/out of the modified file from
   unchanged files are still correct.
2. Compare the incremental result against a full from-scratch re-index of the same post-change
   repo state — the two graphs should be equivalent (same node/edge sets, modulo insertion
   order).
3. Confirm `WorkflowBuilder`/`DomainDetector` output after an incremental run matches a full
   rebuild's output on the same end-state.
