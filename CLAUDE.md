# lumen — Project Context for Claude

## What this project is

**lumen** illuminates opaque codebases. It takes a source code repository and produces
documentation, architecture diagrams, and migration roadmaps using LLMs and a knowledge graph.

```
Source repo → [indexer] → KuzuDB graph → [pipeline/agent] → Markdown artifacts → [builder] → Docusaurus site
                                     ↘ [ui] → Graph visualization (React + Sigma.js)
```

This is a monorepo containing three sub-projects:

| Directory | Former repo | Purpose |
|---|---|---|
| `pipeline/` | `reverse-eng-agent` | Python LLM pipeline: indexer stage, agent (supervisor+subagents), Docusaurus builder |
| `indexer/` | `code-mem-graph` | Indexer runtimes: Java fat JAR, JS parser, Python parser |
| `ui/` | `code-mem-graph-ui` | React + Express graph visualization UI |

---

## How to install and run

### Docker (recommended — no local prerequisites)

```bash
make docker-build                        # builds lumen pipeline image
make docker-run REPO=/path/to/repo       # Anthropic Claude

# Ollama (local model — host.docker.internal bridges container → host)
make compose-pipeline REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"

make compose-docs    # serve generated doc-site → http://localhost:8080
make compose-ui      # graph visualization UI  → http://localhost:3001
```

### Native install

```bash
make install-indexer   # runs indexer/install.sh — requires Java 21, Node 18, Python 3
make install-pipeline  # pip install -e pipeline/

cd pipeline
lumen run /path/to/repo --verbose

make dev-ui            # Vite (port 5173) + Express (port 3001) dev server
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
| `docker-compose.yml` | Three profiles: `pipeline`, `docs`, `ui` |

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
| `pipeline` | `make compose-pipeline REPO=...` | — (writes to `./output/`) |
| `docs` | `make compose-docs` | http://localhost:8080 |
| `ui` | `make compose-ui` | http://localhost:3001 |

`pipeline` service has `extra_hosts: host.docker.internal:host-gateway` for Ollama on Linux.
On Mac/Windows Docker Desktop, `host.docker.internal` is available automatically.

---

## Sub-project: pipeline/

Python package named `codedoc` (internal). CLI entry point: `lumen` (via `pyproject.toml`).

Key files:
- `pipeline/codedoc/cli.py` — Click CLI (`lumen run`)
- `pipeline/codedoc/config.py` — config loader; defaults use `Path(__file__)` relative paths
- `pipeline/codedoc/pipeline.py` — sequential orchestration: indexer → agent → builder
- `pipeline/codedoc/stages/agent.py` — supervisor + parallel subagents
- `pipeline/codedoc/llm.py` — LLM abstraction: `ClaudeProvider`, `OllamaProvider`, `OpenAIProvider`
- `pipeline/codedoc/kg_tools/toolkit.py` — `ReverseEngineerToolkit` (36 graph query tools)
- `pipeline/codedoc/kg_tools/backends.py` — `KuzuBackend`, `Neo4jBackend`
- `pipeline/codedoc/prompts/re-prompt.md` — base agent system prompt
- `pipeline/codedoc/prompts/phase{2,3,4}-*.md` — phase-specific task overrides
- `pipeline/scripts/build-docs-site.sh` — builds MkDocs Material site from artifacts
- `pipeline/.codedoc.toml` — runtime config (`indexer_bin_dir = ../indexer/bin`)
- `pipeline/pyproject.toml` — package name: `lumen`, entry point: `lumen = "codedoc.cli:main"`

Agent architecture:
```
run_supervisor_agent()
  ├─ Phase 1: get_architecture_summary()             ← direct graph call, no LLM
  ├─ Phase 2: run_loop(subagent/api-analyst)          ┐ parallel threads
  ├─ Phase 3: run_loop(subagent/architect)            ┘ each gets own KuzuBackend
  └─ Phase 4: run_loop(subagent/migration-planner)   ← seeded with Phase 2+3 output
```

Artifacts produced (100–250 lines each):
```
current-state/inventory.md        ← API surface, modules, tech stack (ONLY place for tech stack)
architecture/system-overview.md
domain/domain-analysis.md
migration/roadmap.md              ← no calendar dates
target-state/blueprint.md
target-state/openapi/<ctx>.yaml   ← optional
manifests/artifacts.json
```

---

## Sub-project: indexer/

Gradle multi-project (Java + embedded JS/Python parsers). Builds wrapper scripts in `indexer/bin/`.

Key files:
- `indexer/install.sh` — builds all runtimes, generates `bin/cmg-{java,js,python,mcp}` wrappers
- `indexer/app/` — Java/Kotlin fat JAR (Gradle Shadow, main: `code.graph.App`)
- `indexer/parsers/javascript/parse.js` — Babel-based JS/TS parser
- `indexer/parsers/python/parse.py` — Python AST parser
- `indexer/mcp/` — MCP server

`install.sh` uses `$SCRIPT_DIR` — re-run after cloning or moving the repo. Generated
`indexer/bin/` wrappers contain absolute paths and are gitignored.

---

## Sub-project: ui/

React 19 + TypeScript + Vite frontend with Sigma.js/Graphology graph visualization.
Express 5 backend connects to KuzuDB or Neo4j.

Key files:
- `ui/src/App.tsx` — three-panel layout: QueryPanel | GraphCanvas | NodeDetailPanel
- `ui/server/index.ts` — Express server (port 3001): `/api/connect`, `/api/query`, `/api/schema`
  - In production (`NODE_ENV=production`): also serves built React `dist/` as static files
- `ui/server/kuzu-service.ts` — KuzuDB adapter
- `ui/server/neo4j-service.ts` — Neo4j adapter
- `ui/vite.config.ts` — in dev mode proxies `/api` → port 3001

Dev: `cd ui && npm run dev` (Vite port 5173 + Express port 3001)
Docker: `make compose-ui` → Express serves everything on port 3001

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
| jlink instead of GraalVM native-image | KuzuDB extracts JNI `.so` from JAR at runtime; native-image can't handle this |
| No PyInstaller; pip install directly | PyInstaller bundles cause GLIBC mismatch on aarch64 between python:3.11-slim and node:20-slim |
| `python:3.11-slim` as final Docker base | Same glibc as python-deps-builder; Node binary copied from node:20-slim (backwards-compatible) |
| No `pkg`/Node SEA for JS parser | KuzuDB uses `process.dlopen()` on real `.node` file paths; can't virtualise |
| Docker runtime `.codedoc.toml` at `/workspace/` | Overrides `indexer_bin_dir` + `build_script` without code changes |
| `ui/server/index.ts` production static serving | Single port (3001) for both API and React app in Docker |
| `extra_hosts: host.docker.internal:host-gateway` | Lets pipeline container reach host Ollama on Linux |
| LangGraph removed from pipeline | 3 sequential nodes, no branching — 50 MB dep for zero benefit |
| Parallel Phase 2+3 subagents | Cuts wall-clock time ~50%; phases use disjoint tool sets |
| Each subagent gets its own KuzuBackend | KuzuDB connections are not thread-safe |
| `get_method_source` capped at 15 calls | Graph queries ~100–300 tokens; source reads ~1,000–6,000 |
| No calendar dates in roadmap | Fabricated timelines damage credibility |
| MkDocs Material instead of Docusaurus | Python-based (~10 MB vs ~200 MB), no npm in pipeline; `mkdocs-material` in pyproject.toml |
