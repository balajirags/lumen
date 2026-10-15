# Multi-Repo / Cross-Service Knowledge Graph

## Background

User request: index multiple connected microservice repos (an ecommerce platform split into
products, orders, inventory, payment, cart, customer, ... repos) into one knowledge graph, in
order to extract flows/specs that span more than one repo (e.g. "place order" touching cart →
orders → inventory → payment).

Today this is impossible. Confirmed by reading the code (not guessed):

- Every layer is hard-wired to exactly one repo per run: `PipelineState`/`init_state`
  (`pipeline/codedoc/pipelines/common.py`) carries singular `repo_path`/`repo_name`/`kuzu_path`
  fields; `ReverseEngineerToolkit.__init__` (`kg_tools/toolkit.py:49-53`) takes exactly one
  `backend`; `mcp_server.build_mcp_server` (`mcp_server.py:47-125`) opens exactly one
  `KuzuBackend(db_path)`. No list-of-repos concept exists anywhere.
- Grepped the whole repo (code, docs, `backlog/`) for `microservice`/`multi-repo`/`federat`/
  `cross-repo`/`cross-service` — zero hits. This has never been discussed or planned before.
- No indexer (Java/JS/Python/PHP) recognizes an *outbound* HTTP call as a distinct thing.
  `WorkflowBuilder.java`'s terminal detection (`isJvmTerminal`, L104-113; PHP terminals,
  L136-144) only matches DB/repository/event-broker vocabulary
  (`.repository.`/`producer`/`kafkatemplate`/Eloquent/PDO). RestTemplate, Feign, WebClient,
  axios, fetch, `requests`/`httpx`, Guzzle — none of these are recognized anywhere; grepping the
  whole `indexer/` tree for these strings returns zero matches. A call like
  `restTemplate.getForObject(url, ...)` today just looks like an ordinary unresolved `CALLS`
  edge to an external/library method. `external=true` means "declaration outside this repo's own
  source" (unresolved import/library symbol), not "this is a network call to another service" —
  there's no field for that distinction anywhere in the schema.
- `Neo4jBackend` (Python side, `kg_tools/backends.py:32-46`) is real code but never
  instantiated anywhere (`cli.py`/`pipelines/*.py`/`mcp_server.py`/`stages/*.py` all use
  `KuzuBackend` exclusively) — not a usable federation layer today. The Java indexer does have a
  live, working `Neo4jGraphStore.java`, but the Python pipeline never passes `-b neo4j` or any
  `--neo4j-*` flag to it, so it's unreachable from the current CLI/pipeline/MCP layer.
- KuzuDB schema creation is `CREATE TABLE IF NOT EXISTS` (additive, idempotent) and — critically
  — **a single repo's run already writes multiple languages into one shared DB directory**: for
  a polyglot repo, the Java/JS/Python/PHP indexer binaries all resolve to the same
  `{repo-name}-db` path and interleave writes into it in one run
  (`indexer/parsers/python/parse.py:766-773`, `indexer/parsers/javascript/parse.js:1020-1034`,
  `indexer/app/.../App.java:102-120`, `indexer/parsers/php/parse.php:740-741` all derive
  `{db-path}/{repo-name}-db` the same way). So "N repos sharing one physical graph" is a proven
  pattern already in production use — it just currently has no `repo`-scoping property on any
  node/rel table, and the DB directory name is *derived from* `--repo-name`, which is exactly
  the value that needs to vary per service for multi-repo to make sense.
- Node ids are typically file-path/qualifiedName-derived and are **not** globally unique across
  repos (e.g. two services scaffolded from the same template can both produce a node id for
  `src/main/java/.../HealthController.java:HealthController`). `KuzuStore.save()`'s node insert
  is `CREATE`, not an upsert (`store.py:180`), and `KuzuStore._query()` swallows all exceptions
  (`store.py:257-261`) — so an id collision across two repos sharing one DB would silently drop
  a node with zero error, the same class of bug already found once in this codebase for
  `HAS_ANNOTATION` (see CLAUDE.md's design-decisions table).

## Decisions already made with the user

- **Resolution strategy**: pure heuristic URL matching, no user-supplied service manifest —
  accepted lower resolution accuracy in exchange for zero setup cost.
- **Languages**: polyglot — HTTP-client detection should cover Java, JS/TS, and Python in the
  first pass (PHP/Guzzle as a fast-follow, same pattern).
- **Delivery**: phased, foundation first. Three stages (below); only Stage 1 has a concrete
  file-level plan so far.
- **Output**: both markdown artifacts (Stage 3) and interactive MCP queries (Stage 1 gets this
  almost for free once the shared graph exists).

## Roadmap

```
Stage 1: multi-repo indexing → one shared, repo-scoped KuzuDB graph
         → queryable today via `lumen mcp --db-path <shared db>`

Stage 2: outbound-HTTP-call detection per language (RestTemplate/Feign/WebClient,
         axios/fetch, requests/httpx) + a post-processing pass (sibling to
         WorkflowBuilder/DomainDetector) that heuristically matches a caller's
         URL/path literal against another indexed service's routes, emitting a
         new cross-service edge with a confidence score (mirroring CALLS'
         confidence/reason pattern) — unresolved calls (env-var/config-driven
         URLs) are still recorded as "outbound, unresolved" rather than dropped.

Stage 3: new pluggable pipeline (`lumen cross-service`, following the
         security-audit pattern) with its own agent stage that reads the
         Stage 2 cross-service edges and writes markdown artifacts — a
         service-dependency map and a handful of cross-service journey docs
         (e.g. place-order-flow.md).
```

## Stage 1 — shared, repo-scoped multi-repo graph (ready to implement)

### 1. Indexer: `repo` scoping + id namespacing (store layer)

Every store implementation gets the same two additive changes: a new `repo STRING` column on
every node/rel table, and an id-prefixing step so two repos sharing one physical DB can never
collide on primary key.

- `indexer/parsers/python/store.py`
  - `KuzuStore.__init__` (`store.py:132-139`) gains a `repo_name` param.
  - `init_schema()` (`store.py:141-154`): add `repo STRING` to the per-table DDL string (loop at
    `store.py:142-152`) and to `with_normalized_rel_props()` (`store.py:21-25`, which already
    injects `language/kind/normKind` into every `REL_DEFINITIONS` entry the same way).
  - `save()` (`store.py:171-237`): at the top, rewrite every `node['id']` and every
    `rel['sourceId']`/`rel['targetId']` in-place to `f"{self.repo_name}::{original_id}"`; add
    `'repo': self.repo_name` to the `props` dict (`store.py:185-190`) and `'repo'` to
    `valid_keys` (`store.py:191-194`).
  - This same `KuzuStore` class is reused by the PHP bridge, so this one change covers Python
    *and* PHP indexing.
- `indexer/parsers/javascript/store.js` — mirror: `repo STRING` column in the schema string at
  `store.js:81`, id-prefixing + `repo` prop in the `save()` path around `store.js:154`.
- `indexer/app/src/main/java/code/graph/store/KuzuGraphStore.java` — mirror in `initSchema()`
  (`:56-61` node columns, `:74-103` rel columns) and the node/rel write methods (`:162` and the
  rel-write equivalent).
- `indexer/parsers/php/kuzu_writer.py` — add `--repo-name` to its own `argparse` (currently only
  `--db-path`/`--json-file`/`--clear`, `kuzu_writer.py:18-20`) and pass it into `KuzuStore(...)`
  (`kuzu_writer.py:26`); update `indexer/parsers/php/store.php`'s invocation of `kuzu_writer.py`
  to forward the `$repoName` it already computes at `parse.php:740`.

### 2. Indexer: decouple the physical DB directory name from `--repo-name`

Every entrypoint today derives the DB directory as `{db-path}/{repo-name}-db`
(`indexer/parsers/python/parse.py:766-773`, `indexer/parsers/javascript/parse.js:1020-1034`,
`indexer/app/.../App.java:102-120`, `indexer/parsers/php/parse.php:740-741`). For N repos to land
in one shared graph, they need to converge on one directory while still keeping distinct
`--repo-name` values (for the `repo` property/id-prefix above). Add one new optional flag,
`--db-name`, to all four entrypoints: when given, `{db-path}/{db-name}-db` is used instead of
`{repo-name}-db`; when omitted, behavior is byte-for-byte unchanged (defaults to `--repo-name`).
This is the only indexer-CLI-facing change; every existing single-repo invocation is unaffected
since no caller passes `--db-name` yet.

### 3. Pipeline: thread `db_name` through, add the multi-repo orchestrator

- `pipeline/codedoc/pipelines/common.py`: add an optional `db_name: str | None = None` field to
  `PipelineState`/`init_state(...)` (defaults to `None`, i.e. "use `repo_name`" — no change for
  `full.py`/`mcp.py`/`security_audit.py`, which never pass it).
- `pipeline/codedoc/stages/indexer.py`: use `state.db_name or effective_repo_name` everywhere the
  code currently hardcodes `effective_repo_name` for path purposes — the `cmd` construction
  (`:108`, add `--db-name` when set) and the expected-directory checks (`:149`, `:159`, `:170`).
  Keep passing `--repo-name` unchanged (still the per-service label for schema stamping).
- New `pipeline/codedoc/pipelines/multi_repo.py`: `run_pipeline(repos: list[tuple[str, str]],
  output_dir, cluster_name, ...)`.
  - `create_run_dir(output_dir, cluster_name)` **once** for the whole run (reused helper).
  - For each `(path, service_name)`: build a state via `init_state(repo_path=path,
    repo_name=service_name, db_name=cluster_name, ...)` sharing the same run dir, then
    `run_preflights(state)` and `run_indexer(state)` (both reused unmodified — preflight is
    already a pure per-path function, and indexer now honors `db_name`).
  - No agent stage in Stage 1 (mirrors `pipelines/mcp.py`'s shape: preflight + indexer, then
    stop). Aggregate a small `services: [{name, path, node_count, ...}]` list (reusing metrics
    already computed by preflight per repo) into one `pipeline.json` via `finalize_state(...)`,
    `mode="multi-repo"`.
  - Print a trailing hint mirroring `mcp.py`'s pattern: which `lumen mcp --db-path <shared db>`
    command to run next.
- `pipeline/codedoc/cli.py`: new `@main.command(name="index-multi")`. Accepts a repeatable
  `--repo` option in `PATH` or `PATH:NAME` form (name defaults to `Path(path).name` — no
  manifest file required, matching the "no manifest" decision), a `--name` option for the
  cluster/run label (default `"multi-repo"`), and only the subset of options that are actually
  used (`--output-dir`, `--repo-size-check`, `--allow-xlarge`) — no `--model`/`--provider`/
  `--max-turns`/`--timeout`, since Stage 1 has no LLM agent stage.

### 4. Toolkit / MCP: minimal repo-awareness (unlocks the "interactive MCP" output mode now)

- `pipeline/codedoc/kg_tools/toolkit.py`: add one new tool, `get_indexed_repos()` (`MATCH (n)
  RETURN DISTINCT n.repo`-style query, or equivalent), so an MCP client can discover which
  services live in a shared graph. Add an optional `repo: str | None = None` filter parameter to
  the four tools most useful for cross-service exploration first — `get_entry_points`,
  `get_api_endpoints`, `get_domains`, `get_workflows` — rather than mechanically retrofitting all
  ~50 tools; extend the rest on demand once real usage shows which ones need it.
- `pipeline/codedoc/mcp_server.py`: no structural change required — it already takes one
  `db_path`, and that path now simply *may* point at a shared multi-repo graph. Optionally tweak
  `format_server_identity` to note when multiple `repo` values are present.

### 5. Make target (native path only for Stage 1)

- Add `lumen-index-multi` to the `Makefile`, calling `uv run lumen index-multi ...` — mirrors
  `lumen-run`/`lumen-security-audit`.
- **Deferred, not silently dropped**: Docker support (`lumen-docker-index-multi`). The existing
  `lumen-docker-run.sh`/`lumen-docker-security-audit.sh` pattern mounts exactly one `REPO=` path
  at `/repo`; multi-repo needs multiple `-v` mounts, which doesn't fit that single-mount
  convention and needs its own small design pass. Native (`uv run lumen index-multi`) is the
  Stage 1 target; Docker follows once the native path is validated.

### What's explicitly out of scope for Stage 1

- No outbound-HTTP-call detection, no cross-service edge, no route-matching — that's Stage 2.
- No new agent stage / markdown artifacts — that's Stage 3.
- No retrofitting all ~50 toolkit tools with a `repo` filter — only the 4 listed above.
- No Docker entrypoint for the new command yet.

## Verification (for Stage 1)

1. Create/reuse 2–3 tiny fixture repos (existing `e2e-test/fixtures/` single-service fixtures
   work) and run:
   `uv run lumen index-multi --repo fixtures/svcA:orders --repo fixtures/svcB:payments --output-dir /tmp/multi-out --name ecommerce-demo`.
2. Confirm exactly **one** `index.kuzu/ecommerce-demo-db` directory is created (not two).
3. Open the resulting DB directly and confirm `MATCH (n) RETURN DISTINCT n.repo` returns exactly
   `{orders, payments}`, and that total node counts equal the sum of running each fixture through
   the existing single-repo `lumen run` indexer separately (no silent id-collision drops).
4. `lumen mcp --db-path <shared db>` starts; call `get_indexed_repos()` and
   `get_entry_points(repo="orders")` (direct toolkit call or via an MCP client) and confirm
   repo-scoped filtering works.
5. Regression: run `./e2e-test/test-lumen.sh` — Stage 1 touches shared store/indexer files used
   by every existing pipeline (`run`, `security-audit`, `mcp`), so this must still pass unchanged.
6. Add a `pipeline/tests/` unit test for `multi_repo.run_pipeline` with two fake repo dirs,
   asserting one shared run dir/db path and that `run_indexer` is invoked twice with matching
   `db_name`.
7. Update `CLAUDE.md` (pipeline file table, pipelines list, Makefile targets) and `README.md` as
   part of this change, per existing project convention — not a follow-up.
