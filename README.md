# lumen

**Illuminate any codebase.** lumen reverse-engineers a source repository into a full
documentation site — architecture diagrams, domain model, migration roadmap — without
reading source files en masse.

---

## How it works

Most AI documentation tools feed raw source files into an LLM context window. That approach
fails at scale: a medium Java service (50k–200k lines) exhausts any context budget before
the LLM can reason meaningfully about architecture.

lumen takes a different path:

```
Source repo
    │
    ▼
[Indexer] — static analysis → Code Property Graph (KuzuDB)
    │         AST + CFG + DFG    call graph, hierarchy, coupling
    │
    ▼
[Multi-agent pipeline] — graph queries first, source reads second
    │
    ├─ Supervisor coordinates 3 parallel subagents
    ├─ Subagent 1: API surface + module inventory
    ├─ Subagent 2: Architecture + domain model       } run in parallel
    └─ Subagent 3: Migration risk + target state     } seeded by 1 + 2
    │
    ▼
[Builder] → MkDocs Material documentation site
         → KuzuDB graph (explorable in the UI)
```

### Why a Code Property Graph instead of file reading

A **Code Property Graph (CPG)** is a static analysis index that encodes AST, control
flow, and data flow into a single queryable graph. The LLM agents query this graph
instead of reading files — extracting structure for a fraction of the token cost:

| What you need to know | File reading | Graph query |
|---|---|---|
| Class hierarchy | Read every class file | `get_class_hierarchy` — ~200 tokens |
| Who calls a method | `grep` + manual trace | `get_callers` — ~100 tokens |
| Hotspot components | Read + count coupling manually | `get_hotspots` — ~300 tokens |
| All API endpoints | Scan controllers line-by-line | `get_api_endpoints` — ~150 tokens |
| Circular dependencies | Multi-file import tracing | `detect_circular_dependencies` — ~200 tokens |

**Full architecture analysis via graph: ~5,000–10,000 tokens.**
**Equivalent raw-file analysis: 500,000–2,000,000+ tokens — often impossible.**

For the few methods where business logic matters (pricing rules, security checks, complex
state machines), lumen uses `get_method_source` — which uses the graph's own line-number
metadata to read only the exact method body (50–600 tokens), not the whole file.

### Multi-agent synthesis

The pipeline runs three specialised subagents under a supervisor:

- **api-analyst** — entry points, endpoints, module structure, entities
- **architect** — coupling matrix, design patterns, domain model, external systems
- **migration-planner** — hotspots, dead code, risk matrix, target state blueprint

Phase 2 (api-analyst) and Phase 3 (architect) run **in parallel** on independent
graph tool sets. Phase 4 (migration-planner) runs after both complete, seeded with
their findings — no redundant queries, no repeated analysis.

---

## What you get

| Document | What it covers |
|---|---|
| API & Module Inventory | Endpoints, packages, domain entities, tech stack |
| System Architecture | Layered architecture, coupling hotspots, data flow, design patterns |
| Domain Analysis | Business capabilities, bounded contexts, domain events |
| Migration Roadmap | Risk matrix, dead code candidates, modernization phases |
| Target State Blueprint | Target microservice map and migration principles |

Plus a **visual graph explorer** (the UI) for ad-hoc Cypher queries on the CPG.

---

## Quickstart (Docker — no local prerequisites)

```bash
git clone <repo-url> lumen && cd lumen
make docker-build
cp .env.example .env    # add ANTHROPIC_API_KEY
make docker-run REPO=/path/to/your/repo
```

### With a local Ollama model

Inside Docker, `localhost` is the container — not your machine. Use
`host.docker.internal` to reach your host's Ollama:

```bash
# Mac/Windows: host.docker.internal is automatic
# Linux: docker-compose.yml already sets extra_hosts

make compose-pipeline REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"
```

---

## Viewing results

Output lands in `./output/` after every run.

| Service | URL | Command |
|---|---|---|
| MkDocs doc-site | http://localhost:8081 | `make compose-docs` |
| Graph UI (Docker) | http://localhost:3002 | `make compose-ui` |
| Graph UI (dev) | http://localhost:5174 | `make dev-ui` |

### Doc-site

```bash
make compose-docs
# → http://localhost:8081
```

### Graph UI

```bash
make compose-ui
# → http://localhost:3002
# Connect → DB type: KuzuDB, DB path: /data/<repo>-<timestamp>/index.kuzu
```

(`/data` inside the container maps to `./output/` on your host.)

---

## Native install

Requires Java 21, Node 18, Python 3.11+.

```bash
cp .env.example .env    # add ANTHROPIC_API_KEY
make install            # builds indexer + installs CLI
cd pipeline
lumen run /path/to/repo --verbose
```

With Ollama (native):

```bash
lumen run /path/to/repo \
  --provider ollama --model qwen2.5:32b \
  --base-url http://localhost:11434/v1
```

Graph UI in dev mode:

```bash
make dev-ui    # Vite → http://localhost:5174  |  Express → http://localhost:3002
```

---

## CLI options

```
lumen run REPO_PATH [OPTIONS]

  --model TEXT        LLM model (default: claude-sonnet-4-6)
  --provider TEXT     auto | anthropic | ollama | openai
  --base-url TEXT     Override API endpoint (e.g. http://localhost:11434/v1)
  --output-dir TEXT   Output directory (default: ./codedoc-output)
  --max-turns INT     Max LLM turns per phase (default: 40)
  --verbose           Stream logs as the pipeline runs
```

---

## Output structure

```
codedoc-output/<repo-name>-<timestamp>/
├── pipeline.json          ← run metadata and token usage
├── index.kuzu/            ← Code Property Graph database
├── artifacts/             ← 7 markdown documents
│   ├── current-state/inventory.md
│   ├── architecture/system-overview.md
│   ├── domain/domain-analysis.md
│   ├── migration/roadmap.md
│   ├── target-state/blueprint.md
│   └── manifests/artifacts.json
└── doc-site/              ← built MkDocs Material site
```

---

## Configuration

`pipeline/.codedoc.toml` sets persistent defaults (CLI flags always win):

```toml
[pipeline]
model     = "claude-sonnet-4-6"
max_turns = 40

[paths]
output_dir = "./my-output"
```

---

## Supported languages

| Language | Indexer |
|---|---|
| Java / Kotlin | JavaParser + Kotlin Compiler PSI → fat JAR |
| JavaScript / TypeScript / React | Babel + custom CFG/DFG walker |
| Python | `ast` module + custom CFG/DFG walker |

---

## Cost

A typical run on a medium-sized repo (~50k lines) uses roughly 15,000–25,000 tokens
for graph queries plus up to 90,000 tokens for targeted source reads.

At Claude Sonnet pricing: approximately **$0.10–$0.25 per run**.

Token usage is recorded in `pipeline.json` after every run.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
