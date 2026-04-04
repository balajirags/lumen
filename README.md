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
[Multi-agent pipeline] — Analyst + Architect pattern
    │
    ├─ Phase 1: Orientation (direct graph call — no LLM)
    │
    ├─ Phase 2: 3 parallel Analysts (graph queries + write artifacts)
    │   ├─ Domain Analyst      → business capabilities + ER diagram
    │   ├─ Flows Analyst       → user journeys + C4 context + API spec
    │   └─ Tech Analyst        → coupling hotspots + decomposition signals
    │
    └─ Phase 3: Architect (reads Phase 2 artifacts → writes target state)
               → bounded context decomposition + target C4 + strangler fig plan
    │
    ▼
[Builder] → MkDocs Material documentation site (PlantUML diagrams)
         → KuzuDB graph (explorable in the UI)
```

### What’s new in the current pipeline

- **Multi-language indexing per run** — supported Java/Kotlin, JS/TS, and Python slices are all indexed in the same run when present.
- **Normalized graph metadata** — nodes and edges carry `language`, `kind`, and `normKind` so tooling can reason across language-specific parser outputs more consistently.
- **Repo metrics guardrail** — a native preflight plugin estimates repo size using LOC, source-file count, and language mix before indexing starts.
- **Archetype-aware prompting** — the agent now selects `backend-service`, `frontend-app`, or `library` guidance before the analyst phase.
- **Improved CLI UX** — indexing shows live per-language progress, and the three parallel analysts render as separate live boxes during Phase 2.

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

The pipeline uses an **Analyst + Architect** pattern:

- **Domain Analyst** — queries domain model, capabilities, validation rules, entity relationships
- **Flows Analyst** — traces user flows, integration points (upstream + downstream), API surface
- **Tech Analyst** — coupling hotspots, dead code, circular dependencies, decomposition signals

All three analysts run **in parallel**, each writing their own artifacts directly to disk.
The **Architect** then reads those artifacts and designs the target state — bounded context
decomposition, target C4 diagram, and strangler fig extraction plan.

Diagrams are produced in **PlantUML** (C4Context + sequence diagrams) rendered to SVG in the
built documentation site.

### Repo metrics guardrail

Before indexing, lumen runs a native preflight size check. It does **not** use an LLM.

The default plugin measures:

- total non-empty LOC in supported source files
- supported source-file count
- language mix across Java/Kotlin, JS/TS, and Python

It classifies the repo size/risk and warns when the repo is large relative to the current
analysis settings such as `max_turns` and `max_context_tokens`.

Guardrail modes:

- `warn` — default; show warning and continue
- `strict` — fail before indexing on high-risk repos
- `off` — disable the guardrail entirely

The preflight is implemented as a **pluggable module** inside the pipeline, so teams can
remove or replace it without changing the indexer or agent stages.

---

## What you get

| Document | What it covers |
|---|---|
| Business Capabilities | All capabilities in the system + business rules and validations per capability |
| Business Journeys | Key user flows as "As a [role], I can [action]…" + PlantUML sequence diagrams |
| C4 System Context | Integration map — upstream callers + downstream dependencies + protocols (PlantUML) |
| Coupling Hotspots | Risk matrix, coupling pairs, dead code candidates, decomposition seam candidates |
| ER Diagram | Entity relationships and bounded context ownership (PlantUML, backend only) |
| API Spec | OpenAPI YAML (backend only, when endpoint signatures are available) |
| Bounded Contexts | Bounded context decomposition grounded in coupling + domain evidence |
| Target C4 Context | Future decomposed state as PlantUML C4Context diagram |
| Strangler Fig Plan | Ordered extraction plan with seam identification and routing strategy |
| Repo Metrics | Preflight LOC / file-count / language-mix assessment with size/risk classification |

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
  --repo-size-check   off | warn | strict (default: warn)
  --verbose           Stream logs as the pipeline runs
```

---

## Output structure

```
output/<repo-name>-<timestamp>/
├── pipeline.json          ← run metadata, repo metrics, and token usage
├── index.kuzu             ← Code Property Graph database (KuzuDB)
└── artifacts/             ← documentation artifacts
    ├── domain/
    │   ├── business-capabilities.md  ← capabilities + business rules per capability
    │   └── er-diagram.md             ← PlantUML entity diagram (backend only)
    ├── architecture/
    │   ├── business-journeys.md      ← PlantUML sequence diagrams for key flows
    │   └── c4-context.md             ← PlantUML C4Context (upstream + downstream)
    ├── tech/
    │   └── coupling-hotspots.md      ← hotspot table + dead code + seam candidates
    ├── current-state/
    │   └── api-spec.yaml             ← OpenAPI spec (backend only, conditional)
    ├── target-state/
    │   ├── bounded-contexts.md       ← BC decomposition + service table
    │   ├── c4-target.md              ← PlantUML C4Context of future decomposed state
    │   └── strangler-fig.md          ← ordered extraction plan
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
repo_size_check = "warn"

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

Mixed-language repos are indexed in a single run. The resulting graph keeps parser-native
labels and also stores normalized metadata so the agent/tooling can work across languages.

## CLI experience

When you run the pipeline in a TTY:

- the repo metrics preflight is shown before indexing
- the indexer renders a live per-language progress panel
- the three analyst agents render as separate live boxes during Phase 2
- supervisor and final stage messages still print as normal log lines

---

## Cost

A typical run on a medium-sized repo (~50k lines) uses roughly 150,000–300,000 tokens
across the three analysts and the architect (graph queries + artifact writing).

At Claude Sonnet pricing: approximately **$0.20–$0.50 per run**.

Token usage is recorded in `pipeline.json` after every run.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
