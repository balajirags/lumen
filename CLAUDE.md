# lumen — Project Context for Claude

## What this project is

**lumen** illuminates opaque codebases. It takes a source code repository and produces
documentation, architecture diagrams, and migration roadmaps using LLMs and a knowledge graph.

```
Source repo → [preflight + indexer] → KuzuDB graph
                                      ├─ [full pipeline] -> [agent] -> Markdown artifacts -> [builder] -> MkDocs Material site
                                      ├─ [mcp pipeline] -> MCP server (stdio)
                                      ├─ [mcp-http pipeline] -> MCP server (Streamable HTTP)
                                      └─ [ui] -> Graph visualization (React + Sigma.js)
```

This is a monorepo containing three sub-projects:

| Directory | Former repo | Purpose |
|---|---|---|
| `pipeline/` | `reverse-eng-agent` | Python LLM pipeline: indexer stage, agent (supervisor+subagents), MkDocs builder |
| `indexer/` | `code-mem-graph` | Indexer runtimes: Java fat JAR, JS parser, Python parser |
| `ui/` | `code-mem-graph-ui` | React + Express graph visualization UI |

---

## How to install and run

### Docker (recommended — no local prerequisites)

```bash
make docker-build                        # builds lumen pipeline image
make docker-run REPO=/path/to/repo       # Anthropic Claude

# Ollama (local model — host.docker.internal bridges container → host)
make docker-pipeline REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"

make docker-mcp REPO=/path/to/repo PORT=8765

make compose-docs    # serve generated doc-site → http://localhost:8081
make compose-ui      # graph visualization UI  → http://localhost:3002
```

### Native install

```bash
make install-indexer   # runs indexer/install.sh — requires Java 21, Node 18, Python 3
make install-pipeline  # cd pipeline && uv sync

# Run the pipeline (ARGS required: specify provider + model)
make run REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'
make run REPO=/path/to/repo ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'

make dev-docs          # builds doc-site + serves at http://localhost:8081
make dev-ui            # Vite (port 5174) + Express (port 3002) dev server
```

Copy `.env.example` → `.env` and set `ANTHROPIC_API_KEY`.

---

## Configuration

Config priority: CLI flags → `pipeline/.codedoc.toml` → built-in defaults

Key defaults (`pipeline/codedoc/config.py`):

| Key | Default |
|---|---|
| `model` | `claude-sonnet-4-6` |
| `provider` | `auto` |
| `max_turns` | 60 |
| `repo_size_check` | `warn` |
| `indexer_bin_dir` | `../indexer/bin` (monorepo); `/usr/local/bin` (Docker, via `.codedoc.toml`) |
| `agent_prompt` | `./codedoc/prompts/re-prompt.md` |
| `build_script` | `../scripts/build-docs-site.sh` (monorepo); `/opt/lumen/scripts/...` (Docker) |

Docker runtime overrides `indexer_bin_dir` and `build_script` via `/workspace/.codedoc.toml`
baked into the image — `load_config()` reads `.codedoc.toml` from CWD at startup.

---

## Docker files

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage pipeline image: jlink JRE + PyInstaller binaries + Node JS parser |
| `Dockerfile.ui` | Multi-stage UI image: Vite build + tsx Express server |
| `docker-compose.yml` | Four profiles: `pipeline`, `mcp`, `docs`, `ui` |

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

### Dockerfile.ui — 2 stages

| Stage | Output |
|---|---|
| `ui-builder` | `npm run build` → `dist/` (React static files) |
| final | `node:20-slim` + `dist/` + Express server (`npx tsx server/index.ts`) |

`ui/server/index.ts` serves `dist/` as static files when `NODE_ENV=production`, so the
single Express process on port 3001 handles both API routes and the React app.

### docker-compose.yml profiles

| Profile | Command | URL |
|---|---|---|
| `pipeline` | `make docker-pipeline REPO=... ARGS='...'` | — (writes to `./output/`) |
| `mcp` | `make docker-mcp REPO=... PORT=8765` | http://localhost:8765/mcp |
| `docs` | `make compose-docs` | http://localhost:8081 |
| `ui` | `make compose-ui` | http://localhost:3002 |

`pipeline` service has `extra_hosts: host.docker.internal:host-gateway` for Ollama on Linux.
On Mac/Windows Docker Desktop, `host.docker.internal` is available automatically.
`mcp-http` uses the same image and host bridge, but runs `lumen mcp-http` with `--host 0.0.0.0`.
Normal MCP commands already print config snippets before serving; `--print-config` is only for config-only output.

---

## Sub-project: pipeline/

Python package named `codedoc` (internal). CLI entry point: `lumen` (via `pyproject.toml`).

Key files:
- `pipeline/codedoc/cli.py` — Click CLI (`lumen run`, `lumen mcp`, `lumen mcp-http`), includes MCP serve flags
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
- `pipeline/codedoc/mcp_server.py` — MCP server backed by `kg_tools`; supports stdio and Streamable HTTP, plus native/Docker/client config output
- `pipeline/codedoc/llm.py` — LLM abstraction: `ClaudeProvider`, `OllamaProvider`, `OpenAIProvider`
- `pipeline/codedoc/kg_tools/toolkit.py` — `ReverseEngineerToolkit` (36 graph query tools)
- `pipeline/codedoc/kg_tools/backends.py` — `KuzuBackend`, `Neo4jBackend`
- `pipeline/codedoc/prompts/analyst-domain.md` — Business Analyst system prompt (writes 2 artifacts)
- `pipeline/codedoc/prompts/analyst-flows.md` — Integration Architect system prompt (writes 2–3 artifacts)
- `pipeline/codedoc/prompts/analyst-tech.md` — Staff Engineer system prompt (writes 1 artifact)
- `pipeline/codedoc/prompts/architect.md` — Solution Architect system prompt (writes target-state artifacts; manifest is machine-generated)
- `pipeline/codedoc/prompts/archetype-*.md` — archetype overlays for `backend-service`, `frontend-app`, and `library`
- `pipeline/codedoc/prompts/re-prompt.md` — single-agent fallback prompt (monolithic execution path)
- `pipeline/scripts/build-docs-site.sh` — builds MkDocs Material site with PlantUML; supports multi-repo accumulation
- `pipeline/.codedoc.toml` — runtime config (`indexer_bin_dir = ../indexer/bin`, `max_turns = 60`, `repo_size_check = "warn"`)
- `pipeline/pyproject.toml` — package name: `lumen`, entry point: `lumen = "codedoc.cli:main"`, uses `uv`

Full pipeline architecture (Analyst + Architect pattern):
```
run_supervisor_agent()
  ├─ Phase 1: get_architecture_summary()       ← direct graph call, no LLM
  │
  ├─ Phase 2: 3 parallel Analyst agents        ← each has graph tools + write_artifact
  │   ├─ analyst/domain  (Business Analyst)    → domain/business-capabilities.md
  │   │                                        → domain/er-diagram.md
  │   ├─ analyst/flows   (Integration Arch.)   → architecture/business-journeys.md
  │   │                                        → architecture/c4-context.md
  │   │                                        → [current-state/api-spec.yaml]
  │   └─ analyst/tech    (Staff Engineer)      → tech/coupling-hotspots.md
  │
  └─ Phase 3: Architect agent                  ← reads Phase 2 artifacts; write_artifact only
                                               → target-state/bounded-contexts.md
                                               → target-state/c4-target.md
                                               → target-state/strangler-fig.md
```

Each analyst has its own `KuzuBackend` (connections are not thread-safe).
Analyst turn contract: explicit TURN 1/2/3/N sequence in `user_request`; `max_source_reads=0`.
Architect receives Phase 2 artifact content injected into its system prompt (up to 10k chars each).
`manifests/artifacts.json` is machine-written by the pipeline, not the model.
Before Stage 1, the pipeline may run pluggable native preflights; the default one is repo metrics.
Agent prompting is archetype-aware: `backend-service`, `frontend-app`, or `library`.

MCP pipeline architecture:
```
run_mcp_pipeline()
  ├─ Preflight: repo metrics
  ├─ Stage 1: indexer
  └─ Prepare MCP command + stdio server config snippets
```

HTTP MCP pipeline architecture:
```
run_mcp_http_pipeline()
  ├─ Preflight: repo metrics
  ├─ Stage 1: indexer
  └─ Prepare HTTP URL + native/Docker/client config snippets
```

Artifacts produced (PlantUML diagrams):
```
domain/business-capabilities.md   ← capabilities + business rules/validations per capability
domain/er-diagram.md              ← PlantUML entity diagram + bounded context ownership table [conditional]
architecture/business-journeys.md ← 3–5 business user journeys with PlantUML sequence diagrams
architecture/c4-context.md        ← PlantUML C4Context: upstream + downstream + protocols
tech/coupling-hotspots.md         ← hotspot table + coupling pairs + dead code + seam candidates
current-state/api-spec.yaml       ← OpenAPI spec [conditional: backend with endpoint signatures]
target-state/bounded-contexts.md  ← BC decomposition + service responsibility table
target-state/c4-target.md         ← PlantUML C4Context of future decomposed state
target-state/strangler-fig.md     ← ordered extraction plan grounded in hotspot data
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

## Sub-project: ui/

React 19 + TypeScript + Vite frontend with Sigma.js/Graphology graph visualization.
Express 5 backend connects to KuzuDB or Neo4j.

Key files:
- `ui/src/App.tsx` — three-panel layout: QueryPanel | GraphCanvas | NodeDetailPanel
- `ui/server/index.ts` — Express server (port 3002): `/api/connect`, `/api/query`, `/api/schema`
  - In production (`NODE_ENV=production`): also serves built React `dist/` as static files
- `ui/server/kuzu-service.ts` — KuzuDB adapter
- `ui/server/neo4j-service.ts` — Neo4j adapter
- `ui/vite.config.ts` — in dev mode proxies `/api` → port 3001

Dev: `cd ui && npm run dev` (Vite port 5174 + Express port 3002)
Docker: `make compose-ui` → Express serves everything on port 3002

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
| Separate full, MCP, and MCP HTTP pipeline modules | Keeps `lumen run`, `lumen mcp`, and `lumen mcp-http` independent while reusing shared setup/finalization helpers |
| HTTP MCP is additive, not a replacement | URL-based MCP is the simplest client UX, but stdio remains useful for local process-based clients |
| Multi-language indexing in one run | Real repos are often polyglot; warning-only detection was too weak |
| Normalized graph metadata (`language`, `kind`, `normKind`) is additive | Preserve parser-native fidelity while improving toolkit/agent consistency |
| Repo archetype prompt overlays | Backend-only prompting was too brittle for frontend and library repos |
| Machine-written `artifacts.json` manifest | Prevents LLM hallucination of repo metadata and timestamps |
| jlink instead of GraalVM native-image | KuzuDB extracts JNI `.so` from JAR at runtime; native-image can't handle this |
| No PyInstaller; pip install directly | PyInstaller bundles cause GLIBC mismatch on aarch64 between python:3.11-slim and node:20-slim |
| `python:3.11-slim` as final Docker base | Same glibc as python-deps-builder; Node binary copied from node:20-slim (backwards-compatible) |
| No `pkg`/Node SEA for JS parser | KuzuDB uses `process.dlopen()` on real `.node` file paths; can't virtualise |
| Docker runtime `.codedoc.toml` at `/workspace/` | Overrides `indexer_bin_dir` + `build_script` without code changes |
| `ui/server/index.ts` production static serving | Single port (3002) for both API and React app in Docker |
| `extra_hosts: host.docker.internal:host-gateway` | Lets pipeline container reach host Ollama on Linux |
| LangGraph removed from pipeline | 3 sequential nodes, no branching — 50 MB dep for zero benefit |
| Analyst + Architect pattern | Analysts write artifacts directly via `write_artifact` (reliable); avoids fragile note-passing via custom tools |
| Each analyst gets its own KuzuBackend | KuzuDB connections are not thread-safe |
| `max_source_reads=0` for analysts | Analysts need only graph structure; source reads add latency and cost without benefit |
| Explicit TURN N contracts in user_request | Prescriptive turn sequences prevent analysts going off-script; proven more reliable than open-ended instructions |
| Architect reads artifact files (not notes) | Architect gets formatted markdown input, not raw research notes; higher quality target-state output |
| Rich live progress UX in CLI | Indexer and analyst phases should feel active during long runs |
| PlantUML via mkdocs-build-plantuml-plugin | PlantUML C4 stdlib (`!include <C4/C4_Context>`) gives semantically richer C4 diagrams; renders as SVG at build time |
| plantuml.jar downloaded in java-builder stage | Java already present for indexer; reuses same JRE; no extra base image needed |
| No calendar dates in roadmap | Fabricated timelines damage credibility |
| MkDocs Material instead of Docusaurus | Python-based (~10 MB vs ~200 MB), no npm in pipeline; `mkdocs-material` in pyproject.toml |
| `uv` instead of `pip` | Faster installs, reproducible lockfile (`uv.lock`); `make install-pipeline` runs `uv sync` |
| `--repo-name` CLI flag | Docker mounts repo at `/repo` so `Path("/repo").name = "repo"`; flag lets caller override |
| Output dir `<repo>-<timestamp>` | Allows multiple runs of the same repo side-by-side without overwriting |
| Multi-repo doc-site | `build-docs-site.sh` iterates `output/*/artifacts/` — accumulates all runs in one site |
