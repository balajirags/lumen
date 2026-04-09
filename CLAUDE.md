# lumen — Project Context for Claude

## What this project is

**lumen** illuminates opaque codebases. It takes a source code repository and produces
documentation, architecture diagrams, and migration roadmaps using LLMs and a knowledge graph.
The graph-first architecture is intended to improve token and cost scaling on medium and
large repos, and to reduce follow-up analysis cost when the same indexed repo is reused via MCP.

```
Source repo → [preflight + indexer] → KuzuDB graph
                                      ├─ [full pipeline] -> [agent] -> Markdown artifacts -> [builder] -> MkDocs Material site
                                      └─ [mcp pipeline] -> MCP server (Streamable HTTP)
```

This is a monorepo containing two sub-projects:

| Directory | Former repo | Purpose |
|---|---|---|
| `pipeline/` | `reverse-eng-agent` | Python LLM pipeline: indexer stage, agent (supervisor+subagents), MkDocs builder |
| `indexer/` | `code-mem-graph` | Indexer runtimes: Java fat JAR, JS parser, Python parser |

---

## How to install and run

### Docker (recommended — no local prerequisites)

```bash
make lumen-docker-build                  # builds lumen pipeline image
make lumen-docker-run REPO=/path/to/repo \
  ARGS="--provider anthropic --model claude-sonnet-4-6"

# Ollama (local model — host.docker.internal bridges container → host)
make lumen-docker-run REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"

make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
make lumen-docker-docs

make lumen-docker-docs  # serve generated doc-site → http://localhost:8081
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

### Native install

```bash
make lumen-install-indexer   # runs indexer/install.sh — requires Java 21, Node 18, Python 3
make lumen-install-pipeline  # cd pipeline && uv sync

# Run the pipeline (ARGS required: specify provider + model)
make lumen-run REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'
make lumen-run REPO=/path/to/repo ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'
```

Set provider credentials via environment variables such as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.

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
| `Dockerfile` | Multi-stage pipeline image: jlink JRE + PyInstaller binaries + Node JS parser |
| `scripts/lumen-docker-*.sh` | Script-backed Docker entrypoints for pipeline, MCP, docs, image load, and release bundling |

### Dockerfile (pipeline) — 4 stages

| Stage | Base | Output |
|---|---|---|
| `java-builder` | `eclipse-temurin:21-jdk-jammy` | Gradle shadowJar + jlink minimal JRE (~70 MB) |
| `node-builder` | `node:20-slim` | `npm install --omit=dev` for JS parser |
| `python-deps-builder` | `python:3.11-slim` | `pip install --prefix=/deps` for lumen + cmg-python deps |
| final | `python:3.11-slim` | All artifacts assembled; Node binary copied from `node:20-slim` |

Using `python:3.11-slim` as the final base (instead of `node:20-slim`) avoids GLIBC mismatches
on aarch64 where `python:3.11-slim` requires GLIBC_2.38 but `node:20-slim` only has 2.36.
`lumen` runs as a pip-installed entry point — no PyInstaller needed.

### Docker entrypoints

| Profile | Command | URL |
|---|---|---|
| `run` | `make lumen-docker-run REPO=... ARGS='...'` | — (writes to `./output/`) |
| `mcp` | `make lumen-docker-mcp DB=...` | http://localhost:8765/mcp |
| `docs` | `make lumen-docker-docs` | http://localhost:8081 |

`pipeline`, `mcp`, and `docs` are now script-backed `docker run` entrypoints invoked from the Makefile.
They all use the same `DOCKER_IMAGE` runtime.
The scripts add `host.docker.internal:host-gateway` so Ollama-on-host works on Linux; on Mac/Windows Docker Desktop, `host.docker.internal` is available automatically.
`lumen-docker-mcp` prefers serving an existing DB via `DB`, and falls back to repo indexing when `REPO` is provided.
`lumen-docker-docs` serves `output/doc-site` from the same `lumen` image instead of a separate generic Python image.
Normal MCP commands already print config snippets before serving; `--print-config` is only for config-only output.

---

## Sub-project: pipeline/

Python package named `codedoc` (internal). CLI entry point: `lumen` (via `pyproject.toml`).

Key files:
- `pipeline/codedoc/cli.py` — Click CLI (`lumen run`, `lumen mcp`), includes MCP serve flags
- `pipeline/codedoc/cli.py` — Click CLI (`lumen run`, `lumen mcp`), includes MCP serve flags
- `pipeline/codedoc/config.py` — config loader; defaults use `Path(__file__)` relative paths
- `pipeline/codedoc/pipelines/full.py` — full docs pipeline: preflight → indexer → agent → builder
- `pipeline/codedoc/pipelines/mcp.py` — MCP pipeline: preflight → indexer → MCP serve metadata
- `pipeline/codedoc/pipelines/mcp_http.py` — HTTP MCP pipeline: preflight → indexer → HTTP MCP serve metadata
- `pipeline/codedoc/pipelines/common.py` — shared run-dir, state-init, and finalization helpers
- `pipeline/codedoc/pipeline.py` — compatibility shim exporting the pipeline entrypoints
- `pipeline/codedoc/preflight/repo_metrics.py` — native pluggable repo metrics guardrail (LOC, file count, language mix)
- `pipeline/codedoc/preflight/runner.py` — preflight registry/runner; pipeline core depends on this, not on repo-metrics directly
- `pipeline/codedoc/stages/agent.py` — supervisor + parallel analysts + architect
- `pipeline/codedoc/log.py` — structured progress logging, indexer progress panel, repo metrics panel, analyst live boxes
- `pipeline/codedoc/mcp_server.py` — MCP server backed by `kg_tools`; supports Streamable HTTP plus native/Docker/client config output
- `pipeline/codedoc/llm.py` — LLM abstraction: `ClaudeProvider`, `OllamaProvider`, `OpenAIProvider`
- `pipeline/codedoc/kg_tools/toolkit.py` — `ReverseEngineerToolkit` (36 graph query tools)
- `pipeline/codedoc/kg_tools/backends.py` — `KuzuBackend`, `Neo4jBackend`
- `pipeline/codedoc/prompts/analyst-domain.md` — Business Analyst system prompt (writes 2 artifacts)
- `pipeline/codedoc/prompts/analyst-flows.md` — Integration Architect system prompt (writes 2–3 artifacts)
- `pipeline/codedoc/prompts/analyst-tech.md` — Staff Engineer system prompt (writes 1 artifact)
- `pipeline/codedoc/prompts/architect.md` — Solution Architect system prompt (writes target-state artifacts; manifest is machine-generated)
- `pipeline/codedoc/prompts/archetype-*.md` — archetype overlays for `backend-service`, `frontend-app`, `fullstack-app`, and `library`
- `pipeline/codedoc/prompts/re-prompt.md` — single-agent fallback prompt (monolithic execution path)
- `pipeline/scripts/build-docs-site.sh` — builds MkDocs Material site with Mermaid plus deterministic C4 PlantUML for C1 context views; supports multi-repo accumulation
- `pipeline/.codedoc.toml` — runtime config (`indexer_bin_dir = ../indexer/bin`, `max_turns = 60`, `repo_size_check = "warn"`)
- `pipeline/pyproject.toml` — package name: `lumen`, entry point: `lumen = "codedoc.cli:main"`, uses `uv`

Full pipeline architecture (researcher fan-out + architect):
```
run_supervisor_agent()
  ├─ Phase 1: get_architecture_summary()       ← direct graph call, no LLM
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
Architect receives Phase 2 artifact content injected into its system prompt (up to 20k chars each).
`manifests/artifacts.json` is machine-written by the pipeline, not the model.
Before Stage 1, the pipeline may run pluggable native preflights; the default one is repo metrics.
Pipeline classification is pipeline-owned and normalized into `primary_repo_type`, `capabilities`, and an `artifact_plan`.
Repo types currently supported in the prompt layer are `backend-service`, `frontend-app`, `fullstack-app`, and `library`.
For `xlarge` repos, the full docs pipeline stops after preflight and directs the user to MCP mode instead of indexing, unless `--allow-xlarge` is set.

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
domain/business-capabilities.md   ← capabilities + business rules/validations per capability
domain/er-diagram.md              ← Mermaid ER diagram + bounded context ownership table [required for backend/fullstack]
architecture/business-journeys.md ← 3–5 business user journeys with Mermaid sequence diagrams
architecture/c4-context.md        ← deterministic PlantUML C4Context rendered from structured data
architecture/route-map.md         ← UI route/screen inventory when frontend route evidence is strong
tech/coupling-hotspots.md         ← hotspot table + coupling pairs + dead code + seam candidates
current-state/ui-to-api-interactions.md ← UI/component to API/client interaction view
current-state/module-dependency-map.md  ← dependency and seam summary
current-state/api-spec.yaml       ← OpenAPI spec [required for backend/fullstack]
target-state/bounded-contexts.md  ← BC decomposition + service responsibility table (backend-service)
target-state/strangler-fig.md     ← ordered extraction plan grounded in hotspot data (backend-service)
target-state/fullstack-boundaries.md ← frontend/backend seam plan (fullstack-app)
target-state/migration-plan.md    ← migration plan (frontend/fullstack/library)
manifests/artifacts.json          ← machine-generated index of all artifacts written
```

---

## Sub-project: indexer/

Gradle multi-project (Java + embedded JS/Python parsers). Builds wrapper scripts in `indexer/bin/`.

Key files:
- `indexer/install.sh` — builds all runtimes, generates `bin/cmg-{java,js,python}` wrappers
- `indexer/app/` — Java/Kotlin fat JAR (Gradle Shadow, main: `code.graph.App`)
- `indexer/parsers/javascript/parse.js` — Babel-based JS/TS parser
- `indexer/parsers/python/parse.py` — Python AST parser

`install.sh` uses `$SCRIPT_DIR` — re-run after cloning or moving the repo. Generated
`indexer/bin/` wrappers contain absolute paths and are gitignored.

Current indexing behavior:
- The pipeline detects all supported languages present in the repo and runs all relevant indexers in one pipeline run.
- The graph preserves parser-native labels and also stores normalized metadata on nodes/edges:
  `language`, `kind`, `normKind`
- This normalized metadata is used by the toolkit and agent layer for more consistent cross-language reasoning.

---

## KuzuDB conventions (used across all sub-projects)

- `label(n)` not `labels(n)[0]`
- `label(r)` not `type(r)` for relationship types
- No `shortestPath()` — use `MATCH path = (a)-[*1..N]->(b)`
- After DISTINCT/aggregation, ORDER BY must use column aliases
- `PARENT -[:CONTAINS]-> CHILD` direction

---

## Design decisions (do not revert)

| Decision | Reason |
|---|---|
| Monorepo, no nx/turborepo | Three independent build systems; a Makefile is sufficient |
| No pnpm workspaces | JS parser (`indexer/parsers/javascript`) is private and tiny |
| `indexer/install.sh` unchanged | Already uses `$SCRIPT_DIR`; relocatable as-is |
| `indexer_bin_dir = ../indexer/bin` | Wires pipeline to indexer binaries across monorepo boundary |
| Repo metrics is a preflight plugin, not core pipeline logic | Easy for the team to disable/remove/replace without changing indexer/agent stages |
| Separate full and MCP pipeline modules | Keeps `lumen run` and `lumen mcp` independent while reusing shared setup/finalization helpers |
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
