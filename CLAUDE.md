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

```bash
# 1. Build indexer runtimes (Java/JS/Python parsers)
make install-indexer         # runs indexer/install.sh — requires Java 21, Node 18, Python 3

# 2. Install Python pipeline
make install-pipeline        # pip install -e pipeline/

# 3. Run the full pipeline
cd pipeline
codedoc run /path/to/repo --verbose

# 4. Run the graph UI (optional)
make dev-ui                  # starts Vite (port 5173) + Express (port 3001)
```

**Or both at once:**
```bash
make install
```

**Prerequisites:**
- Python 3.11+ (for pipeline)
- Java 21+ (for Java/Kotlin indexing)
- Node 18+ (for JS/TS indexing and UI)
- `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`)

---

## Configuration

Config priority: CLI flags → `pipeline/.codedoc.toml` → built-in defaults

Key defaults (`pipeline/codedoc/config.py`):

| Key | Default |
|---|---|
| `model` | `claude-sonnet-4-6` |
| `provider` | `auto` |
| `max_turns` | 60 |
| `indexer_bin_dir` | `../indexer/bin` (relative to `pipeline/`) |
| `agent_prompt` | `./codedoc/prompts/re-prompt.md` |

---

## Sub-project: pipeline/

Python package named `codedoc`. Entry point: `codedoc run`.

Key files:
- `pipeline/codedoc/cli.py` — Click CLI
- `pipeline/codedoc/pipeline.py` — sequential orchestration: indexer → agent → builder
- `pipeline/codedoc/stages/agent.py` — supervisor + parallel subagents (Phase 2/3 parallel, Phase 4 sequential)
- `pipeline/codedoc/llm.py` — LLM abstraction: `ClaudeProvider`, `OllamaProvider`, `OpenAIProvider`
- `pipeline/codedoc/kg_tools/toolkit.py` — `ReverseEngineerToolkit` (36 graph query tools)
- `pipeline/codedoc/kg_tools/backends.py` — `KuzuBackend`, `Neo4jBackend`
- `pipeline/codedoc/prompts/re-prompt.md` — base agent system prompt
- `pipeline/codedoc/prompts/phase{2,3,4}-*.md` — phase-specific task overrides
- `pipeline/scripts/build-docs-site.sh` — scaffolds + builds Docusaurus site

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

`indexer/install.sh` uses `$SCRIPT_DIR` to derive all paths — re-run it after cloning or moving the repo.
The generated `indexer/bin/` wrappers contain absolute paths and are gitignored.

---

## Sub-project: ui/

React 19 + TypeScript + Vite frontend with Sigma.js/Graphology graph visualization.
Express 5 backend that connects to KuzuDB or Neo4j.

Key files:
- `ui/src/App.tsx` — three-panel layout: QueryPanel | GraphCanvas | NodeDetailPanel
- `ui/server/index.ts` — Express server (port 3001): `/api/connect`, `/api/query`, `/api/schema`
- `ui/server/kuzu-service.ts` — KuzuDB adapter
- `ui/server/neo4j-service.ts` — Neo4j adapter
- `ui/vite.config.ts` — proxies `/api` to port 3001

Start: `cd ui && npm run dev` (concurrently runs Vite + Express)

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
| LangGraph removed from pipeline | 3 sequential nodes, no branching — 50 MB dep for zero benefit |
| Parallel Phase 2+3 subagents | Cuts wall-clock time ~50%; phases use disjoint tool sets |
| Each subagent gets its own KuzuBackend | KuzuDB connections are not thread-safe |
| `get_method_source` capped at 15 calls | Graph queries ~100–300 tokens; source reads ~1,000–6,000 |
| No calendar dates in roadmap | Fabricated timelines damage credibility |
