# lumen — Project Context for Claude

## What this project is

**lumen** illuminates opaque codebases. It takes a source code repository and produces
documentation, architecture diagrams, and migration roadmaps using LLMs and a knowledge graph.
The graph-first architecture is intended to improve token and cost scaling on medium and
large repos, and to reduce follow-up analysis cost when the same indexed repo is reused via MCP.

```
Source repo → [preflight + indexer] → KuzuDB graph
                                      ├─ [full pipeline] -> [agent] -> Markdown artifacts -> [builder] -> MkDocs Material site
                                      ├─ [security-audit pipeline] -> [security-audit agent] -> Markdown artifacts
                                      └─ [mcp pipeline] -> HTTP MCP server
```

Preflight and the indexer are shared, unmodified, by every pipeline. Only the agent stage
(stage 2) is pluggable — new pipelines add a new CLI command + pipeline module + agent-stage
module and reuse preflight/indexer as-is. See "Pluggable agent-stage pipelines" below.

This is a monorepo containing two sub-projects:

| Directory | Former repo | Purpose |
|---|---|---|
| `pipeline/` | `reverse-eng-agent` | Python LLM pipeline: indexer stage, agent (supervisor+subagents), MkDocs builder |
| `indexer/` | `code-mem-graph` | Indexer runtimes: Java fat JAR, JS parser, Python parser, PHP parser |

---

## How to install and run

Three modes: Docker, build-from-source, and pre-built native bundle.
Set provider credentials via `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` environment variables.

### Docker (recommended — no local prerequisites)

```bash
make lumen-docker-build                  # builds lumen pipeline image
make lumen-docker-run REPO=/path/to/repo \
  ARGS="--provider anthropic --model claude-sonnet-4-6"

# Ollama (local model — host.docker.internal bridges container → host)
make lumen-docker-run REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"

make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
make lumen-docker-docs  # serve generated doc-site → http://localhost:8081

# security-audit pipeline (fan-out reviewers + risk synthesis, same indexed graph)
make lumen-docker-security-audit REPO=/path/to/repo \
  ARGS="--provider anthropic --model claude-sonnet-4-6"
```

For `xlarge` repos, `lumen-docker-run` intentionally stops after preflight and recommends
`make lumen-docker-mcp REPO=/path/to/repo` so MCP mode can perform indexing and support focused questions.
The intended user journey is:
1. run `make lumen-docker-run ...`
2. if Lumen stops after preflight for an `xlarge` repo, switch to `make lumen-docker-mcp REPO=/path/to/repo`
3. let MCP mode perform indexing
4. connect an MCP-capable client to `http://127.0.0.1:8765/mcp`
Repo metrics are otherwise informational; the only hard stop is the full pipeline's `xlarge` guardrail.
Set `--allow-xlarge` if you explicitly want to continue the full docs pipeline anyway.
`make lumen-docker-docs` is the only supported docs viewer path. It rebuilds the doc-site from
the existing `./output` directory before serving, so pipeline reruns are not required for docs refreshes.

### Build from source (developer)

Requires Java 21, Node 20, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```bash
make lumen-install          # builds indexer + installs pipeline via uv

# Run the pipeline
make lumen-run REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'
make lumen-run REPO=/path/to/repo ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'

# Or invoke lumen directly
cd pipeline && uv run lumen run /path/to/repo --provider anthropic --model claude-sonnet-4-6

# security-audit pipeline
make lumen-security-audit REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'
cd pipeline && uv run lumen security-audit /path/to/repo --provider anthropic --model claude-sonnet-4-6
```

### Native bundle (no Docker, no dev tools)

Pre-built tarballs bundle JRE + Node + Python venv + all four language parsers (including the
PHP parser with its `vendor/` Composer dependencies). Only `graphviz` is needed on the target
for most repos. PHP repos additionally require a system `php` binary on the target — the PHP
interpreter cannot be bundled, unlike Java (jlink JRE), Node (static binary), and Python (venv).

One-line install (downloads latest release from GitHub):
```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/lumen/main/scripts/install-lumen.sh | bash
```

Build a native tarball from source:
```bash
make lumen-native-build                    # uses version from pyproject.toml
make lumen-native-build VERSION=v1.2.3     # override version
```

Output: `releases/lumen-<version>-<os>-<arch>.tar.gz` with `.sha256` checksum.
The bundle includes `verify.sh` (SHA256 integrity check) and `install.sh` (symlinks into `~/.local/bin`).
`build-native.sh` includes the PHP parser automatically when `vendor/` is present or Composer is
available; it emits a warning and skips PHP if neither is found.

After install:
```bash
lumen run /path/to/repo --provider anthropic --model claude-sonnet-4-6
lumen security-audit /path/to/repo --provider anthropic --model claude-sonnet-4-6
lumen mcp /path/to/repo    # HTTP MCP server

# PHP repos: also install php on the target
# macOS: brew install php
# Linux: sudo apt install php-cli
```

---

## Configuration

Config priority: CLI flags → `pipeline/.codedoc.toml` → built-in defaults

Key defaults (`pipeline/codedoc/config.py`):

| Key | Default |
|---|---|
| `model` | `claude-sonnet-4-6` |
| `provider` | `auto` |
| `timeout` | `300` |
| `max_turns` | 60 |
| `repo_size_check` | `warn` |
| `allow_xlarge` | `false` |
| `indexer_bin_dir` | `../indexer/bin` (monorepo); `/usr/local/bin` (Docker, via `.codedoc.toml`) |
| `agent_prompt` | `./codedoc/prompts/re-prompt.md` |
| `build_script` | `../scripts/build-docs-site.sh` (monorepo); `/opt/lumen/scripts/...` (Docker) |

Docker runtime overrides `indexer_bin_dir` and `build_script` via `/workspace/.codedoc.toml`
baked into the image — `load_config()` reads `.codedoc.toml` from CWD at startup.
Adaptive runtime defaults are applied after preflight when values were not explicitly set:
- `small` / `medium`: keep `timeout = 300`, `max_turns = 60`
- `large` / `xlarge`: use `timeout = 3600`
- full docs pipeline only: `large` / `xlarge` also use `max_turns = 100`
If `timeout` or `max_turns` is explicitly set in `.codedoc.toml` or via CLI, that setting does not adapt.

---

## Docker files

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage pipeline image: jlink JRE + pip-installed lumen runtime + Node JS parser |
| `scripts/lumen-docker-*.sh` | Script-backed Docker entrypoints for pipeline, security-audit, MCP, docs, image load, and release bundling |

### Dockerfile (pipeline) — 5 stages

| Stage | Base | Output |
|---|---|---|
| `java-builder` | `eclipse-temurin:21-jdk-jammy` | Gradle shadowJar + jlink minimal JRE (~70 MB) |
| `node-builder` | `node:20-slim` | `npm install --omit=dev` for JS parser |
| `php-builder` | `php:8.2-cli-alpine` | `composer install --no-dev` for PHP parser vendor |
| `python-deps-builder` | `python:3.11-slim` | `pip install --prefix=/deps` for lumen + cmg-python deps |
| final | `python:3.11-slim` | All artifacts assembled; Node binary + PHP CLI (`php-cli` apt pkg) + vendor copied in; `cmg-php` bridge uses Python already present in the image |

Using `python:3.11-slim` as the final base (instead of `node:20-slim`) avoids GLIBC mismatches
on aarch64 where `python:3.11-slim` requires GLIBC_2.38 but `node:20-slim` only has 2.36.
`lumen` runs as a pip-installed entry point — no PyInstaller needed.

### Docker entrypoints

| Profile | Command | URL |
|---|---|---|
| `run` | `make lumen-docker-run REPO=... ARGS='...'` | — (writes to `./output/`) |
| `security-audit` | `make lumen-docker-security-audit REPO=... ARGS='...'` | — (writes to `./output/`) |
| `mcp` | `make lumen-docker-mcp DB=...` | http://localhost:8765/mcp |
| `docs` | `make lumen-docker-docs` | http://localhost:8081 |

`run`, `security-audit`, `mcp`, and `docs` are script-backed `docker run` entrypoints invoked
from the Makefile. They all use the same `DOCKER_IMAGE` runtime.
`scripts/lumen-docker-security-audit.sh` is a byte-for-byte mirror of `lumen-docker-run.sh`
with only the invoked subcommand changed (`security-audit` instead of `run`) — this is the
pattern any new pluggable pipeline's Docker wrapper should follow.
The scripts add `host.docker.internal:host-gateway` so Ollama-on-host works on Linux; on Mac/Windows Docker Desktop, `host.docker.internal` is available automatically.
`lumen-docker-mcp` prefers serving an existing DB via `DB`, and falls back to repo indexing when `REPO` is provided.
`lumen-docker-docs` serves `output/doc-site` from the same `lumen` image instead of a separate generic Python image.
Normal MCP commands already print config snippets before serving; `--print-config` is only for config-only output.

---

## Sub-project: pipeline/

Python package named `codedoc` (internal). CLI entry point: `lumen` (via `pyproject.toml`).

Key files:
- `pipeline/codedoc/cli.py` — Click CLI behind the repo-local `make lumen-run` and `make lumen-mcp` commands; `common_pipeline_options` decorator shares the repo/model/provider/etc. option set across `run` and other agent-stage pipeline commands (e.g. `security-audit`)
- `pipeline/codedoc/config.py` — config loader; defaults use `Path(__file__)` relative paths
- `pipeline/codedoc/pipelines/full.py` — full docs pipeline: preflight → indexer → agent → builder
- `pipeline/codedoc/pipelines/mcp.py` — MCP pipeline: preflight → indexer → MCP serve metadata
- `pipeline/codedoc/pipelines/security_audit.py` — example pluggable pipeline: preflight → indexer (both unchanged) → security-audit agent stage; no builder step
- `pipeline/codedoc/pipelines/common.py` — shared run-dir, state-init, finalization, and xlarge/runtime-defaults helpers, mode-agnostic (any pipeline module opts in via explicit params, not a hardcoded mode string)
- `pipeline/codedoc/pipeline.py` — compatibility shim exporting the pipeline entrypoints
- `pipeline/codedoc/preflight/repo_metrics.py` — native pluggable repo metrics guardrail (LOC, file count, language mix)
- `pipeline/codedoc/preflight/runner.py` — preflight registry/runner; pipeline core depends on this, not on repo-metrics directly
- `pipeline/codedoc/stages/agent.py` — supervisor + parallel analysts + architect; also exports the reusable `run_loop` tool-loop primitive
- `pipeline/codedoc/stages/parallel.py` — `run_parallel_tasks`, a generic fan-out/fan-in helper (dict of thunks in, dict of results out) any new agent-stage pipeline can reuse
- `pipeline/codedoc/stages/security_audit_agent.py` — example alternative agent stage: 2 parallel reviewers (access-control, dependency-risk) fan out via `run_parallel_tasks`, then 1 fan-in risk-synthesis `run_loop` call
- `pipeline/codedoc/log.py` — structured progress logging, indexer progress panel, repo metrics panel, analyst live boxes
- `pipeline/codedoc/mcp_server.py` — MCP server backed by `kg_tools`; supports the HTTP MCP flow exposed by `make lumen-mcp` and `make lumen-docker-mcp`
- `pipeline/codedoc/llm.py` — LLM abstraction: `ClaudeProvider`, `OllamaProvider`, `OpenAIProvider`
- `pipeline/codedoc/kg_tools/toolkit.py` — `ReverseEngineerToolkit` (40+ graph query tools including `get_workflows`, `get_workflow_steps`, `get_domains`)
- `pipeline/codedoc/kg_tools/backends.py` — `KuzuBackend`, `Neo4jBackend`
- `pipeline/codedoc/prompts/analyst-domain.md` — Business Analyst system prompt (writes 2 artifacts)
- `pipeline/codedoc/prompts/analyst-flows.md` — Integration Architect system prompt (writes 2–3 artifacts)
- `pipeline/codedoc/prompts/analyst-tech.md` — Staff Engineer system prompt (writes 1 artifact)
- `pipeline/codedoc/prompts/architect.md` — Solution Architect system prompt (writes target-state artifacts; manifest is machine-generated)
- `pipeline/codedoc/prompts/archetype-*.md` — archetype overlays for `backend-service`, `frontend-app`, `fullstack-app`, and `library`
- `pipeline/codedoc/prompts/re-prompt.md` — single-agent fallback prompt (monolithic execution path)
- `pipeline/codedoc/prompts/security-analyst-access.md`, `security-analyst-dependencies.md`, `security-synthesis.md` — prompts for the example `security-audit` pipeline's 2 reviewers + fan-in synthesis
- `pipeline/scripts/build-docs-site.sh` — builds MkDocs Material site with Mermaid plus deterministic C4 PlantUML for C1 context views; supports multi-repo accumulation
- `pipeline/.codedoc.toml` — runtime config (`indexer_bin_dir = ../indexer/bin`, `max_turns = 60`, `repo_size_check = "warn"`)
- `pipeline/pyproject.toml` — package name: `lumen`, entry point: `lumen = "codedoc.cli:main"`, uses `uv`

Full pipeline architecture (researcher fan-out + architect):
```
run_supervisor_agent()
  ├─ Phase 1: orientation (direct graph calls, no LLM)
  │   ├─ get_architecture_summary()   ← architecture overview
  │   ├─ get_domains()                ← pre-fetch Domain clusters → embedded in orientation_summary
  │   ├─ get_workflows()              ← pre-fetch Workflow traces → embedded in orientation_summary
  │   └─ get_workflow_steps(name)     ← pre-fetch step chains for top workflows → embedded
  │
  ├─ Phase 2: 3 parallel researcher agents     ← each has graph tools + write_artifact
  │   ├─ analyst/domain  (Business Analyst)    → domain/business-capabilities.md
  │   │                                        → domain/er-diagram.md
  │   ├─ analyst/flows   (Integration Arch.)   → architecture/business-journeys.md
  │   │                                        → architecture/c4-context.md
  │   │                                        → current-state/ui-to-api-interactions.md
  │   │                                        → current-state/api-spec.yaml
  │   └─ analyst/tech    (Staff Engineer)      → tech/coupling-hotspots.md
  │                                            → current-state/module-dependency-map.md
  │
  ├─ Phase 3: synthesis / recovery             ← deterministic backfill + targeted recovery
  │
  └─ Phase 4: Architect + summary              ← target-state + executive summary + manifest
```

Each analyst has its own `KuzuBackend` (connections are not thread-safe).
Analyst turn contract: explicit TURN 1/2/3/N sequence in `user_request`; `max_source_reads=0`.
Architect receives Phase 2 artifact content injected into system prompt (adaptive char limit: 80k total / artifact count, capped at 20k each).
`manifests/artifacts.json` is machine-written by the pipeline, not the model.
`write_artifact` validates the filename against the run's artifact plan; unplanned paths are rejected with a correction hint.
Before Stage 1, the pipeline may run pluggable native preflights; the default one is repo metrics.
Pipeline classification is pipeline-owned and normalized into `primary_repo_type`, `capabilities`, and an `artifact_plan`.
Repo types currently supported in the prompt layer are `backend-service`, `frontend-app`, `fullstack-app`, and `library`.
For `xlarge` repos, the full docs pipeline stops after preflight and directs the user to MCP mode instead of indexing, unless `--allow-xlarge` is set.
Context pruning removes 3 turns at once when over budget by >20k tokens (large/xlarge only); 1 turn for small/medium. Critical evidence (endpoints, workflows) is pre-pinned before the loop for analyst/flows on medium+ repos.

MCP pipeline architecture:
```
run_mcp_pipeline()
  ├─ Preflight: repo metrics
  ├─ Stage 1: indexer
  └─ Prepare HTTP URL + native/Docker/client config snippets
```

Artifacts produced (Mermaid + deterministic C4 PlantUML for C1 context views):
```
summary/executive-summary.md      ← CXO-facing summary, risks, recommendations, confidence/limitations
domain/business-capabilities.md   ← capabilities + business rules/validations; grounded in Domain clusters
domain/er-diagram.md              ← Mermaid ER diagram; JPA annotations for Java, TS class/interface fields for JS/TS [required for backend/fullstack]
architecture/business-journeys.md ← 3–5 journeys with Mermaid sequence diagrams; grounded in Workflow step traces
architecture/c4-context.md        ← deterministic PlantUML C4Context rendered from structured data
architecture/route-map.md         ← UI route inventory [suppressed for JS-frontend repos]
tech/coupling-hotspots.md         ← hotspot table + coupling pairs + dead code + seam candidates
current-state/ui-to-api-interactions.md ← UI/component to API/client interaction view
current-state/module-dependency-map.md  ← dependency and seam summary
current-state/api-spec.yaml       ← OpenAPI spec [required for backend/fullstack; conditional for large/xlarge]
target-state/bounded-contexts.md  ← BC decomposition + service responsibility table (backend-service)
target-state/strangler-fig.md     ← ordered extraction plan grounded in hotspot data (backend-service)
target-state/fullstack-boundaries.md ← frontend/backend seam plan (fullstack-app)
target-state/migration-plan.md    ← migration plan (frontend/fullstack/library)
manifests/artifacts.json          ← machine-generated index of all artifacts written
```

### Pluggable agent-stage pipelines

Adding a new pipeline? Use `docs/adding-a-pipeline.md` — it's a self-contained
reference-and-prompt template (mental model, reusable building blocks, naming conventions,
copy-paste module skeletons, make/Docker/native checklist, verification steps, and a
fill-in-the-blank task section at the end) meant to be handed to whoever implements the
next pipeline so the result is predictable and structurally consistent with
`security-audit`. The summary below is a condensed pointer, not a replacement for it.

`lumen run` is not the only pipeline — new CLI commands can run an entirely different
fan-out/fan-in agent stage against the same preflight+indexer flow, without touching
`stages/agent.py`, the archetype/artifact-plan system, or the docs pipeline in any way.
`lumen security-audit` (`pipelines/security_audit.py` + `stages/security_audit_agent.py`)
is a worked example — copy its shape for a new pipeline:

```
pipelines/<name>.py            ← create_run_dir → init_state(mode="<name>") → run_preflights
                                  → apply_repo_size_runtime_defaults(state, bump_max_turns=...)
                                  → run_indexer (unchanged) → stages/<name>_agent.run_agent
                                  → finalize_state
stages/<name>_agent.py         ← run_agent(state) -> state; builds its own KuzuBackend +
                                  ReverseEngineerToolkit + create_provider(...) per task, fans
                                  out via stages/parallel.run_parallel_tasks, fans in via one
                                  more stages/agent.run_loop call
cli.py                          ← @main.command(name="<name>") + @common_pipeline_options
                                  + a call into pipelines/<name>.run_pipeline
```

Reusable building blocks (no changes needed to use them): `KuzuBackend`,
`ReverseEngineerToolkit` (`kg_tools/`), `create_provider` (`llm.py`), `run_loop` and its
`allowed_artifact_paths`/`phase_label` params (`stages/agent.py`), `run_parallel_tasks`
(`stages/parallel.py`), `common_pipeline_options` (`cli.py`), `pipelines/common.py`'s
run-dir/state/finalization helpers, and `log.py`'s `start_agent_boxes`/`update_agent_box`/
`update_workflow_phase`/`print_researcher_done`/`print_tool_usage_table`/
`print_synthesizer_done`/`stop_agent_boxes` (pass your own role/phase names — this is what
makes a new pipeline's console output look like `lumen run`'s instead of falling back to
plain dim-text lines). A new pipeline defines its own prompt files, its own small
`allowed_artifact_paths` set per `run_loop` call, and its own `run_agent` — there is no
shared "ArchetypeDefinition"-style plan the new pipeline must conform to.

Make/Docker/native parity follows the same copy-the-example pattern as `security-audit`:
- Native + `lumen-install`: nothing to add — `uv run lumen <name>` and the installed
  `lumen <name>` binary work automatically once the CLI command exists (Click resolves any
  registered subcommand; `make lumen-<name> REPO=... ARGS='...'` is just a convenience
  wrapper mirroring `lumen-run`/`lumen-security-audit` in the Makefile).
- Docker: add `scripts/lumen-docker-<name>.sh` as a byte-for-byte copy of
  `lumen-docker-run.sh`/`lumen-docker-security-audit.sh` with the subcommand swapped, plus a
  `lumen-docker-<name>` Makefile target that shells out to it. The Docker image itself needs
  no changes — `ENTRYPOINT ["lumen"]` already resolves any subcommand; the wrapper script only
  exists for the `REPO=`/`ARGS=`/volume-mount convenience the Makefile provides.
- Native bundle build (`build-native.sh`) and the bundle's `lumen` launcher are also
  subcommand-agnostic — no changes needed there for a new pipeline either.

`./e2e-test/test-lumen.sh` is the E2E regression safety net: it discovers every native
`lumen-<name>:` Makefile target (excluding docker/mcp/docs/install/build), runs each one
against the checked-in fixtures under `e2e-test/fixtures/`, and verifies exit code +
`pipeline.json` status/mode + artifacts actually written. A new pipeline is picked up
automatically the moment its Makefile target exists — nothing to edit in the script. This
is separate from `pipeline/tests/` (pytest unit tests with every stage mocked) — the E2E
script exercises the real indexer + real LLM calls end-to-end, which is what caught a real
bug during its own development: `run_indexer` (shared) always populates
`state.artifact_plan` for repo classification, but only the docs/`full` pipeline's agent
stage actually fulfills it — the script's verifier has to know this (`uses_artifact_plan_for`)
rather than inferring "has a plan" from the field's mere presence.

---

## Sub-project: indexer/

Gradle multi-project (Java + embedded JS/Python parsers). Builds wrapper scripts in `indexer/bin/`.

Key files:
- `indexer/install.sh` — builds all runtimes, generates `bin/cmg-{java,js,python,php}` wrappers. Run `make lumen-install` after any indexer code change to rebuild the fat JAR (`./gradlew shadowJar`).
- `indexer/app/` — Java/Kotlin fat JAR (Gradle Shadow, main: `code.graph.App`)
- `indexer/app/src/main/java/code/graph/parser/WorkflowBuilder.java` — post-processing: traces HTTP entry points → repository/event terminals via BFS over CALLS edges; emits `Workflow` + `WORKFLOW_STEP` nodes; includes PHP strategy (Laravel routes + raw PHP superglobals → Eloquent/Repository/DB terminals)
- `indexer/app/src/main/java/code/graph/parser/DomainDetector.java` — post-processing: label propagation over CALLS+CONTAINS graph to cluster cohesive classes; emits `Domain` + `IN_DOMAIN` nodes
- `indexer/parsers/javascript/parse.js` — Babel-based JS/TS parser; creates `Field` nodes for TypeScript class properties and interface members
- `indexer/parsers/python/parse.py` — Python AST parser; creates `Field` nodes for annotated class variables and ORM column assignments
- `indexer/parsers/php/parse.php` — PHP AST parser (nikic/php-parser); extracts classes, interfaces, traits, methods, fields; detects Laravel routes in `routes/*.php` and emits `ANNOTATION_TYPE` nodes + `HAS_ANNOTATION` edges so WorkflowBuilder can trace controller→DB paths; supports `--backend kuzu` (default) and `--backend json`
- `indexer/parsers/php/store.php` — PHP KuzuDB store; bridges graph output to KuzuDB by shelling out to `kuzu_writer.py` (same pattern as `store.py` / `store.js`)
- `indexer/parsers/php/kuzu_writer.py` — thin Python bridge that reads a PHP graph JSON file and writes to KuzuDB using the shared `KuzuStore` class from `indexer/parsers/python/store.py`

`install.sh` uses `$SCRIPT_DIR` — re-run after cloning or moving the repo. Generated
`indexer/bin/` wrappers contain absolute paths and are gitignored.

Current indexing behavior:
- The pipeline detects all supported languages present in the repo and runs all relevant indexers in one pipeline run. Android repos (containing `AndroidManifest.xml`) are detected and rejected early with an informative message.
- The graph preserves parser-native labels and also stores normalized metadata on nodes/edges: `language`, `kind`, `normKind`
- CALLS edges carry `confidence` (0.95 same-file / 0.90 import-resolved / 0.50 global) and `reason` for reliable call-chain filtering.
- Internal nodes have `external=false`; library/placeholder nodes have `external=true`.
- Post-processing runs after all language parsers complete: `WorkflowBuilder` then `DomainDetector` operate on the merged graph.
- JS/TS: TypeScript class property declarations and interface member signatures produce `Field` nodes for ER diagram support.
- JS/TS: For repos with no ORM annotations, `get_domain_model()` falls back to module-path and naming-convention entity detection.
- PHP: Classes, interfaces, traits (emitted as CLASS with `phpKind: "trait"`), methods, and properties are extracted. Laravel routes in `routes/*.php` are parsed and converted to `ANNOTATION_TYPE` nodes + `HAS_ANNOTATION` edges (GetMapping/PostMapping/etc.) on controller methods — matching the same schema `HAS_ANNOTATION` uses for Java Spring annotations. WorkflowBuilder's PHP strategy traces from annotated controller methods to Eloquent Model, Repository, and raw DB/PDO terminals. `cmg-php` writes directly to KuzuDB via `store.php` → `kuzu_writer.py` (Python bridge).
- Route-map and component-boundaries artifacts are suppressed for JS-frontend repos (sparse call graph makes them unreliable).

Release packaging:
- `scripts/lumen-docker-release.sh` packages the current local Docker image into `releases/`
- it always packages `lumen:latest`
- if that image is missing, it runs `make lumen-docker-build`
- `TAG` is only used for release bundle naming
- it does not create git tags automatically

Native distribution:
- `scripts/build-native.sh` builds a self-contained platform tarball (JRE + Node + Python venv + all parsers including PHP); PHP parser `vendor/` is copied from an existing install or re-installed via Composer; skipped with a warning if neither is present
- `scripts/install-lumen.sh` is a secure one-line installer: detects OS/arch, downloads from GitHub Releases, verifies SHA256, extracts to `~/.local/share/lumen/`, symlinks to `~/.local/bin/lumen`
- `make lumen-native-build` invokes `build-native.sh`; supports `VERSION=v1.2.3` override
- `.github/workflows/release.yml` builds platform tarballs on `v*` tags for macOS (arm64, amd64) and Linux (amd64, arm64 via QEMU); the native build job installs PHP+Composer before bundling so the PHP parser is always included in CI-built releases
- `.github/dependabot.yml` runs weekly dependency update PRs for Gradle, npm, pip, and GitHub Actions
- Bundle includes `verify.sh` (SHA256 integrity check) and `install.sh` (symlink into `~/.local/bin`)
- The `lumen` launcher in the bundle auto-generates `.codedoc.toml` with correct absolute paths and warns if graphviz is missing
- The `cmg-php` wrapper in the bundle prepends `venv/bin` to `PATH` before invoking `php` so `store.php::findPython()` uses the bundled Python (with `kuzu` installed) rather than any system Python

Release workflow:
- Version source of truth: `pipeline/pyproject.toml` (`version = "X.Y.Z"`)
- Also updated by bump script: `indexer/parsers/javascript/package.json`, `pipeline/uv.lock`
- `scripts/bump-version.sh <version>` updates all version files and regenerates `uv.lock`
- `make release VERSION=0.2.0` bumps versions, commits, tags — then prints the push command
- `.github/workflows/release.yml` validates tag matches `pyproject.toml` before building
- `lumen --version` reports the installed version via `importlib.metadata`

---

## KuzuDB conventions (used across all sub-projects)

- `label(n)` not `labels(n)[0]`
- `label(r)` not `type(r)` for relationship types
- No `shortestPath()` — use `MATCH path = (a)-[*1..N]->(b)`
- After DISTINCT/aggregation, ORDER BY must use column aliases
- `PARENT -[:CONTAINS]-> CHILD` direction
- Internal nodes: `external = false`. External/library placeholder nodes: `external = true`. Inferred method stubs: `inferred = true`.
- Filter to reliable calls: `WHERE c.confidence >= 0.9` on CALLS edges
- New node types: `Workflow` (id, name, httpMethod, httpPath, entryPointId, terminalId, stepCount, type, language), `Domain` (id, name, heuristicLabel, cohesion, memberCount, language)
- New rel types: `WORKFLOW_STEP {step: INT}`, `IN_DOMAIN`

---

## Design decisions (do not revert)

| Decision | Reason |
|---|---|
| Monorepo, no nx/turborepo | Three independent build systems; a Makefile is sufficient |
| No pnpm workspaces | JS parser (`indexer/parsers/javascript`) is private and tiny |
| `indexer/install.sh` unchanged | Already uses `$SCRIPT_DIR`; relocatable as-is |
| `indexer_bin_dir = ../indexer/bin` | Wires pipeline to indexer binaries across monorepo boundary |
| Repo metrics is a preflight plugin, not core pipeline logic | Easy for the team to disable/remove/replace without changing indexer/agent stages |
| Separate full and MCP pipeline modules | Keeps `make lumen-run` and `make lumen-mcp` independent while reusing shared setup/finalization helpers |
| MCP is HTTP-first | URL-based MCP is the simplest client UX for Docker, local development, and external clients |
| Multi-language indexing in one run | Real repos are often polyglot; warning-only detection was too weak |
| Normalized graph metadata (`language`, `kind`, `normKind`) is additive | Preserve parser-native fidelity while improving toolkit/agent consistency |
| Repo archetype prompt overlays | Backend-only prompting was too brittle for frontend and library repos |
| Machine-written `artifacts.json` manifest | Prevents LLM hallucination of repo metadata and timestamps |
| jlink instead of GraalVM native-image | KuzuDB extracts JNI `.so` from JAR at runtime; native-image can't handle this |
| No PyInstaller; pip install directly | PyInstaller bundles cause GLIBC mismatch on aarch64 between python:3.11-slim and node:20-slim |
| `python:3.11-slim` as final Docker base | Same glibc as python-deps-builder; Node binary copied from node:20-slim (backwards-compatible) |
| No `pkg`/Node SEA for JS parser | KuzuDB uses `process.dlopen()` on real `.node` file paths; can't virtualise |
| Docker runtime `.codedoc.toml` at `/workspace/` | Overrides `indexer_bin_dir` + `build_script` without code changes |
| `extra_hosts: host.docker.internal:host-gateway` | Lets pipeline container reach host Ollama on Linux |
| LangGraph removed from pipeline | 3 sequential nodes, no branching — 50 MB dep for zero benefit |
| Analyst + Architect pattern | Analysts write artifacts directly via `write_artifact` (reliable); avoids fragile note-passing via custom tools |
| Each analyst gets its own KuzuBackend | KuzuDB connections are not thread-safe |
| `max_source_reads=0` for analysts | Analysts need only graph structure; source reads add latency and cost without benefit |
| No fixed "$ per run" claim in docs | Actual cost depends on provider pricing and the input/output token split; use `pipeline.json` token totals for grounded examples |
| Explicit TURN N contracts in user_request | Prescriptive turn sequences prevent analysts going off-script; proven more reliable than open-ended instructions |
| Architect reads artifact files (not notes) | Architect gets formatted markdown input, not raw research notes; higher quality target-state output |
| Rich live progress UX in CLI | Indexer and analyst phases should feel active during long runs |
| Mermaid for journeys/ER + deterministic PlantUML for current-state C4 C1 context diagrams | Mermaid is simpler and more reliable for non-C4 diagrams; only `architecture/c4-context.md` stays on PlantUML and is rendered by code from structured data |
| plantuml.jar downloaded in java-builder stage | Java already present for indexer; reuses same JRE; no extra base image needed |
| No calendar dates in roadmap | Fabricated timelines damage credibility |
| MkDocs Material instead of Docusaurus | Python-based (~10 MB vs ~200 MB), no npm in pipeline; `mkdocs-material` in pyproject.toml |
| `uv` instead of `pip` | Faster installs, reproducible lockfile (`uv.lock`); `make lumen-install-pipeline` runs `uv sync` |
| `--repo-name` CLI flag | Docker mounts repo at `/repo` so `Path("/repo").name = "repo"`; flag lets caller override |
| Output dir `<repo>-<timestamp>` | Allows multiple runs of the same repo side-by-side without overwriting |
| Multi-repo doc-site | `build-docs-site.sh` iterates `output/*/artifacts/` — accumulates all runs in one site |
| Workflow and Domain as post-processing nodes | Derived from the merged graph after all parsers run; both use BFS/label-propagation over CALLS+CONTAINS and are language-agnostic. Pre-fetched in Phase 1 orientation so analysts use pre-loaded data rather than tool calls. |
| Adaptive context pruning (1 vs 3 turns) | Small repos prune 1 turn; large/xlarge prune 3 turns when >20k over budget. Single-turn pruning on small repos prevents the 2× slowdown from agents losing thread and re-exploring. |
| `write_artifact` path validation | Unplanned filenames are rejected (not written) with a correction hint listing allowed paths. Prevents invented paths (architecture/README.md) and wrong-path duplicates (content in current-state/ instead of tech/). |
| api-spec.yaml conditional for large/xlarge | Requires annotated routes or dense call graph; large repos with sparse JS routing reliably fail to produce valid YAML, so it stays optional to avoid marking valid runs as failed. |
| Route-map + component-boundaries suppressed for JS frontend | These artifacts need a typed component tree and resolved call graph; JS repos don't have them so the output was consistently noise. Suppressed in `build_artifact_plan` when `js-runtime` capability is present. |
| Android repos fail fast | `AndroidManifest.xml` detected up to 8 directory levels deep; exits before indexing with a clear message rather than producing a partial/confusing graph. |
| `make lumen-install` rebuilds the JAR | `install.sh` runs `./gradlew shadowJar`. Must be re-run after any Java/Kotlin indexer code change to update `cmg-java`. Gradle's incremental build skips recompilation if no sources changed. |
| Analyst prompts reference pre-loaded orientation sections | Prompts say "if Orientation Summary does NOT contain `## Pre-computed Domains`, call `get_domains` first" — the literal heading is the fallback trigger, not a subjective existence judgement. |
| `npm ci` instead of `npm install` in `indexer/install.sh` | Reproducible builds from the tracked `package-lock.json`; prevents silent dependency drift |
| `package-lock.json` tracked in git | Was gitignored; highest-risk supply chain gap. Now committed so `npm ci` enforces exact versions with SHA512 integrity |
| `uv export --frozen` in `build-native.sh` | Ensures the native bundle installs exact pinned versions from `uv.lock` rather than resolving fresh |
| Upper bounds on Python dependencies | Prevents silent major-version upgrades that could break the pipeline at install time |
| Gradle wrapper SHA256 verification | `distributionSha256Sum` in `gradle-wrapper.properties` detects tampered Gradle distributions |
| Dependabot for all ecosystems | Weekly PRs for Gradle, npm, pip, and GitHub Actions keep dependencies current without manual tracking |
| `build-native.sh` reads version from `pyproject.toml` | Single source of truth; `--version` flag lets CI override for tagged releases |
| SHA256SUMS manifest + `verify.sh` in bundle | Lets users verify integrity of all critical binaries after download or transfer |
| Native `lumen` launcher regenerates `.codedoc.toml` on every run | Bundle stays functional after being moved or renamed — mirrors Docker's `/workspace/.codedoc.toml` mechanism |
| `install-lumen.sh` verifies SHA256 of downloaded tarball | HTTPS + checksum verification; no `eval`, `set -euo pipefail`, supports `VERSION` pinning and custom `LUMEN_INSTALL_DIR` |
| GitHub Actions release workflow on `v*` tags | Builds macOS arm64/amd64 + Linux amd64 natively; Linux arm64 via QEMU. Creates draft GitHub Release with checksums |
| `scripts/bump-version.sh` syncs all version files | Updates pyproject.toml, package.json, regenerates uv.lock, validates all match — single command for version bumps |
| `make release` does NOT auto-push | Pushing tags triggers CI and is irreversible; user confirms manually with the printed command |
| CI version guard in release.yml | `validate-version` job compares tag vs pyproject.toml before building; blocks mismatched releases early |
| `lumen --version` via `importlib.metadata` | Reports the installed package version; closes the verification loop for end users and support |
| PHP traits emitted as CLASS with `phpKind: "trait"` | Avoids adding a new KuzuDB table; DomainDetector clusters traits via existing CLASS branch; `phpKind` property preserves the distinction for consumers that need it |
| PHP store uses a Python bridge (`store.php` → `kuzu_writer.py`) | PHP has no native KuzuDB SDK. Rather than routing through Java, `store.php` shells out to `kuzu_writer.py`, a thin Python script that reuses `KuzuStore` from the Python parser. This keeps `cmg-php` self-contained (same pattern as `cmg-python` / `cmg-js`) and avoids a Java dependency for PHP indexing. Python is always available since it is the pipeline runtime. |
| PHP route annotations emitted as `ANNOTATION_TYPE` (not `DECORATOR`) | `HAS_ANNOTATION` schema requires `Method → AnnotationType` as the target. Emitting `DECORATOR` type nodes would violate the schema and cause KuzuDB insert failures. Using `ANNOTATION_TYPE` also lets `WorkflowBuilder`'s annotation index work identically to the JVM path. |
| `nikic/php-parser` via Composer | Industry-standard PHP AST library (20M+ monthly downloads); same approach as Babel for JS — external library, no built-in PHP tokenizer limitations; `composer.lock` committed for reproducibility |
| PHP `vendor/` directory in `ALWAYS_IGNORED` | Prevents indexing of Composer dependencies (same rationale as `node_modules` for JS) |
| install.sh skips Composer if `vendor/` already present | Allows offline use and avoids requiring Composer on machines where the vendor directory was pre-installed (e.g., from a git-committed vendor or Docker layer) |
| `cmg-php` in native bundle calls system PHP (not bundled) | The PHP interpreter is a compiled binary that varies by OS/arch; jlink solves this for Java, Node ships a single static binary, and Python uses a venv — there is no equivalent portable packaging for PHP. The target machine must have `php` installed for PHP repos. |
| `cmg-php` prepends `venv/bin` to `PATH` | `store.php::findPython()` probes for `python3` / `python` by name. Prepending the bundled venv's `bin/` to `PATH` ensures it finds the bundled Python (which has `kuzu` installed) rather than any system Python that lacks `kuzu`. No changes to `store.php` needed. |
| `pipelines/common.py` xlarge/runtime-default helpers are mode-agnostic | `should_stop_for_xlarge_repo` and `apply_repo_size_runtime_defaults(state, bump_max_turns=...)` no longer branch on `state.mode == "full"`. New pipelines opt in explicitly via the `bump_max_turns` param instead of requiring an edit to shared code every time a pipeline is added. |
| `run_parallel_tasks` extracted as a standalone fan-out/fan-in helper | Generalizes the `ThreadPoolExecutor` pattern already used by the docs pipeline's Phase 2 into a ~10-line, domain-agnostic dict-of-thunks-in/dict-of-results-out function (`stages/parallel.py`), reusable by any new agent-stage pipeline without introducing an "analyst"/"archetype" abstraction. |
| New agent-stage pipelines get their own `run_agent`, not a generalized `run_supervisor_agent` | `run_supervisor_agent`'s `ArchetypeDefinition`/artifact-plan machinery is deeply coupled to the docs pipeline's exact 3-analyst-+-architect contract. Rather than generalizing that (high risk, large surface area), new pipelines write their own small `run_agent(state) -> state` stage module built directly on `KuzuBackend`/`ReverseEngineerToolkit`/`create_provider`/`run_loop`/`run_parallel_tasks` — the docs pipeline stays completely untouched. |
| `common_pipeline_options` Click decorator shared across pipeline commands | `run` and `security-audit` need the identical repo/model/provider/turns/etc. option set; a decorator avoids re-declaring ~10 `@click.option` lines per new pipeline command. |
| `security-audit` pipeline has no builder step | Its artifacts (`security/*.md`) are plain markdown, not part of the MkDocs artifact-plan/manifest contract `stages/builder.py` assumes; skipping the builder keeps the example self-contained. |
| `lumen-security-audit`/`lumen-docker-security-audit` Makefile targets + `scripts/lumen-docker-security-audit.sh` | Gives the new pipeline make/Docker convenience parity with `run`, matching the existing pattern instead of leaving it reachable only via raw `uv run lumen security-audit` / `docker run ... lumen security-audit`. The Docker wrapper script is a deliberate byte-for-byte copy of `lumen-docker-run.sh` with only the subcommand changed — no generic "any pipeline" Make/Docker abstraction, since `ENTRYPOINT ["lumen"]` and the native launcher are already subcommand-agnostic and copying the ~40-line script per pipeline is simpler than parameterizing it. |
| `log.py`'s live fan-out/fan-in dashboard (`start_agent_boxes`, `_render_agent_columns`, `_render_workflow_panel`, `print_progress_line`) is data-driven, not hardcoded | Originally hardcoded to the docs pipeline's exact 3 analyst names + synthesis/architect/summary phases, so `security-audit`'s 2 reviewers never rendered into the live boxes and fell back to plain dim-text lines — visibly inconsistent with `lumen run`. `start_agent_boxes(agent_names=..., workflow_phases=...)` now takes the role/phase names as parameters (defaulting to the docs pipeline's original 3+3, so `lumen run`'s output is byte-for-byte unchanged), and `print_progress_line` routes per-turn events into whichever boxes were registered by membership check instead of `tag.startswith("analyst/"/"architect"/"summary")`. Every new pipeline must call `start_agent_boxes`/`update_agent_box`/`print_researcher_done`/`print_tool_usage_table`/`update_workflow_phase`/`print_synthesizer_done`/`stop_agent_boxes` with its own names (see `security_audit_agent.py` and `docs/adding-a-pipeline.md`'s `run_agent` skeleton) to get this styling — it is not automatic just from using `run_loop`. |
