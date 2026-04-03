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
    ├─ Supervisor coordinates 7 phases
    ├─ Phase 1: Orientation (direct graph call — no LLM)
    ├─ Phase 2: API / component inventory   } run in parallel
    ├─ Phase 3: Architecture + domain model } seeded by Phase 1
    ├─ Phase 4: Migration risk + target state  }
    ├─ Phase 5: C4 system context diagram      } run in parallel
    ├─ Phase 6: Sequence diagrams              } seeded by Phase 2+3
    └─ Phase 7: ER diagram / domain model      }
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

The pipeline runs specialised subagents under a supervisor across 7 phases:

- **api-analyst** (Phase 2) — entry points, endpoints / components, module structure, entities
- **architect** (Phase 3) — coupling matrix, design patterns, domain model, external systems
- **migration-planner** (Phase 4) — hotspots, dead code, risk matrix, target state blueprint
- **c4-context** (Phase 5) — integration points diagram (databases, queues, external APIs)
- **sequence-diagrams** (Phase 6) — Mermaid sequence diagrams for key runtime flows
- **er-diagram** (Phase 7) — entity relationships and bounded context ownership

Phases 2+3 run **in parallel**. Phases 4–7 also run **in parallel**, seeded with Phase 2+3
findings — no redundant queries. The pipeline auto-detects whether the repo is a **frontend**
(React/Vue/Angular) or **backend** (Spring/Django/Express) and selects appropriate prompts.

---

## What you get

| Document | What it covers |
|---|---|
| API & Module Inventory | Endpoints / components, packages, domain entities, tech stack |
| System Architecture | Layered architecture, coupling hotspots, data flow, design patterns |
| C4 System Context Diagram | Integration points: databases, queues, external APIs (Mermaid) |
| Domain Analysis | Business capabilities, bounded contexts, domain events |
| ER Diagram | Entity relationships and bounded context ownership (Mermaid) |
| Sequence Diagrams | Key runtime flows: request handling, auth, data writes (Mermaid) |
| Migration Roadmap | Risk matrix, dead code candidates, modernization phases |
| Target State Blueprint | Target microservice / component map and migration principles |

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
| MkDocs doc-site (Docker) | http://localhost:8081 | `make compose-docs` |
| MkDocs doc-site (native) | http://localhost:8081 | `make dev-docs` |
| Graph UI (Docker) | http://localhost:3002 | `make compose-ui` |
| Graph UI (dev) | http://localhost:5174 | `make dev-ui` |

### Doc-site

```bash
# Docker
make compose-docs    # → http://localhost:8081

# Native
make dev-docs        # builds + serves at http://localhost:8081
```

The doc-site **accumulates** across runs — every repo you analyse appears as a top-level
tab in the navigation. Run against multiple repos and browse them all at once.

### Graph UI

```bash
make compose-ui
# → http://localhost:3002
# Connect → DB type: KuzuDB, DB path: /data/<repo>-<timestamp>/index.kuzu
```

(`/data` inside the container maps to `./output/` on your host.)

---

## Native install

Requires Java 21, Node 18, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env    # add ANTHROPIC_API_KEY
make install            # builds indexer + installs pipeline via uv

# Anthropic Claude
make run REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'

# Ollama (local model)
make run REPO=/path/to/repo ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'

# OpenAI
make run REPO=/path/to/repo ARGS='--provider openai --model gpt-4o'
```

Graph UI in dev mode:

```bash
make dev-ui    # Vite → http://localhost:5174  |  Express → http://localhost:3002
```

---

## CLI options

```
lumen run REPO_PATH [OPTIONS]

  --provider TEXT     auto | anthropic | ollama | openai
  --model TEXT        LLM model (default: claude-sonnet-4-6)
  --base-url TEXT     Override API endpoint (e.g. http://localhost:11434/v1)
  --repo-name TEXT    Override repo name in output dir (useful when mounted at /repo)
  --output-dir TEXT   Output directory (default: ./codedoc-output)
  --max-turns INT     Max LLM turns per phase (default: 60)
  --verbose           Stream logs as the pipeline runs
```

---

## Output structure

```
output/<repo-name>-<timestamp>/
├── pipeline.json          ← run metadata and token usage
├── index.kuzu             ← Code Property Graph database (single file, KuzuDB 0.11+)
└── artifacts/             ← documentation artifacts
    ├── current-state/inventory.md
    ├── architecture/
    │   ├── system-overview.md
    │   ├── c4-context.md         ← Mermaid C4Context diagram
    │   └── sequence-diagrams.md  ← Mermaid sequence diagrams
    ├── domain/
    │   ├── domain-analysis.md
    │   └── er-diagram.md         ← Mermaid erDiagram
    ├── migration/roadmap.md
    ├── target-state/blueprint.md
    └── manifests/artifacts.json

output/doc-site/               ← shared MkDocs Material site (accumulates all runs)
```

---

## Configuration

`pipeline/.codedoc.toml` sets persistent defaults (CLI flags always win):

```toml
[pipeline]
model     = "claude-sonnet-4-6"
provider  = "auto"
max_turns = 60

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
