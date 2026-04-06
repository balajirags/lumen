# lumen

```text
 _                             
| |    _   _ _ __ ___   ___ _ __ 
| |   | | | | '_ ` _ \ / _ \ '_ \
| |___| |_| | | | | | |  __/ | | |
|_____|\__,_|_| |_| |_|\___|_| |_|
```

**Illuminate any codebase.** lumen reverse-engineers a source repository into a full
documentation site — architecture diagrams, domain model, migration roadmap — without
reading source files en masse.

---

## Product

Most AI documentation tools feed raw source files into an LLM context window. That approach
fails at scale: a medium Java service (50k–200k lines) exhausts any context budget before
the LLM can reason meaningfully about architecture.

lumen takes a different path:

```
Source repo
    │
    ▼
[Preflight] — native repo metrics + size guardrail
    │         LOC, file count, language mix, risk band
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
               → bounded context decomposition + strangler fig plan
    │
    ▼
[Builder] → MkDocs Material documentation site (Mermaid + deterministic C4 PlantUML for C1 context views)
         → KuzuDB graph (explorable in the UI)
```

### Supported languages

lumen currently supports:

- Java / Kotlin
- JavaScript / TypeScript
- Python

Multi-language repos are indexed in one run when supported language slices are present.

### Core USP

The main differentiators are:

1. **Token-aware architecture** — lumen minimizes raw source-file stuffing by default, using graph queries and targeted source reads instead. This is most beneficial on medium, large, and repeatedly queried repos.
2. **Code Property Graph over files or plain AST** — lumen indexes the repo into a graph with structural and relationship-aware facts instead of relying on file-by-file reading or a plain syntax tree.
3. **Multi-agent analysis** — lumen uses parallel analysts plus a synthesizing architect, closer to stochastic consensus / distributed research than a single monolithic agent pass.
4. **Model flexibility** — lumen can run with local Ollama models as well as OpenAI and Anthropic models.

In practice, that gives you:

- better token and cost scaling than naive full-repo prompting on medium and large repos
- lower follow-up analysis cost once a repo has been indexed and reused through MCP
- better structural reasoning on medium and large repos
- stronger synthesis through parallel graph-driven investigation
- local or hosted model choice depending on cost, privacy, and quality needs

### Why it is different

- **Multi-language indexing per run** — supported Java/Kotlin, JS/TS, and Python slices are all indexed in the same run when present.
- **Normalized graph metadata** — nodes and edges carry `language`, `kind`, and `normKind` so tooling can reason across language-specific parser outputs more consistently.
- **Repo metrics guardrail** — a native preflight plugin estimates repo size using LOC, source-file count, and language mix before indexing starts.
- **Archetype-aware prompting** — the agent now selects `backend-service`, `frontend-app`, or `library` guidance before the analyst phase.
- **Improved CLI UX** — indexing shows live per-language progress, and the three parallel analysts render as separate live boxes during Phase 2.
- **MCP modes** — `lumen mcp` keeps the stdio flow, and `lumen mcp-http` adds a simpler URL-based MCP server for VS Code, Docker, and cross-workspace use.
- **Split pipeline modules** — the full docs flow and the MCP flow now live in separate pipeline modules with shared setup/finalization helpers.

### Why a Code Property Graph instead of file reading

A **Code Property Graph (CPG)** is a static analysis index that encodes AST, control
flow, and data flow into a single queryable graph. The LLM agents query this graph
instead of reading files. This is intended to scale better than naive full-repo prompting,
especially on medium and large repos or when the same indexed repo is reused repeatedly:

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

### What `lumen run` produces

| Document | What it covers |
|---|---|
| Business Capabilities | All capabilities in the system + business rules and validations per capability |
| Business Journeys | Key user flows as "As a [role], I can [action]…" + Mermaid sequence diagrams |
| C4 System Context | Integration map — upstream callers + downstream dependencies + protocols (deterministic PlantUML) |
| Coupling Hotspots | Risk matrix, coupling pairs, dead code candidates, decomposition seam candidates |
| ER Diagram | Entity relationships and bounded context ownership (Mermaid, backend only) |
| API Spec | OpenAPI YAML (backend only, when endpoint signatures are available) |
| Bounded Contexts | Bounded context decomposition grounded in coupling + domain evidence |
| Strangler Fig Plan | Ordered extraction plan with seam identification and routing strategy |
| Repo Metrics | Preflight LOC / file-count / language-mix assessment with size/risk classification |

Plus a **visual graph explorer** (the UI) for ad-hoc Cypher queries on the CPG.

---

## Quickstart (Docker — recommended)

```bash
git clone <repo-url> lumen && cd lumen
make docker-build
export ANTHROPIC_API_KEY=...
# or: export OPENAI_API_KEY=...
```

### 1. docker-pipeline

```bash
make docker-pipeline REPO=/path/to/your/repo ARGS='--provider anthropic --model claude-sonnet-4-6'
```

If preflight classifies the repo as `xlarge`, `docker-pipeline` stops before indexing and
walks you to MCP mode instead. In that case, continue with:

```bash
make docker-mcp REPO=/path/to/your/repo
```

Step by step for `xlarge` repos:

1. Run `make docker-pipeline ...` as usual.
2. If Lumen stops after preflight, do not rerun `docker-pipeline`.
3. Start MCP mode with `make docker-mcp REPO=/path/to/your/repo`.
4. Let MCP mode perform indexing and expose `http://127.0.0.1:8765/mcp`.
5. Connect your LLM client to that MCP URL and ask focused questions.

Repo metrics are otherwise informational. The hard stop is only the full pipeline's
`xlarge` guardrail.

If you explicitly want to force the full docs pipeline anyway, use:

```bash
make docker-pipeline REPO=/path/to/your/repo \
  ARGS='--allow-xlarge --provider anthropic --model claude-sonnet-4-6'
```

### With a local Ollama model

Inside Docker, `localhost` is the container — not your machine. Use
`host.docker.internal` to reach your host's Ollama:

```bash
# Mac/Windows: host.docker.internal is automatic
# Linux: docker-compose.yml already sets extra_hosts

make docker-pipeline REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"
```

### 2. docker-mcp

If the pipeline output is good enough, stop there. If you want to keep asking questions over MCP,
reuse the same `lumen` image against the DB from that pipeline run:

```bash
make docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
```

`PORT` defaults to `8765`.

If you do not already have pipeline output, this also works:

```bash
make docker-mcp REPO=/path/to/repo
```

That flow:

1. runs preflight
2. indexes the repo
3. exposes a local HTTP MCP server on `http://127.0.0.1:8765/mcp`


### 3. docker-mkdocs

Rebuild and serve the generated site from the same `lumen` image:

```bash
make docker-docs
```

This is the supported docs viewer path. It rebuilds the MkDocs site from the existing
`./output` directory, so you do not need to rerun the pipeline just to refresh docs rendering.

---

## Viewing results

Output lands in `./output/` after every run.

| Service | URL | Command |
|---|---|---|
| MkDocs doc-site (Docker) | http://localhost:8081 | `make docker-docs` |
| MCP HTTP (Docker) | http://localhost:8765/mcp | `make docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db` |
| Graph UI (Docker) | http://localhost:3002 | `make compose-ui` |
| Graph UI (dev) | http://localhost:5174 | `make dev-ui` |

### Doc-site

```bash
# Docker
make docker-docs     # → http://localhost:8081
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

## Native Mode

### 1. Prerequisites

- Java 21
- Node 18
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

Install and configure:

```bash
export ANTHROPIC_API_KEY=...
# or: export OPENAI_API_KEY=...
make install            # builds indexer + installs pipeline via uv
```

### 2. Run with `lumen`

`lumen` is the native CLI. `make` is just a repo-local wrapper around it.

```bash
# Anthropic Claude
cd pipeline
uv run lumen run /path/to/repo --provider anthropic --model claude-sonnet-4-6

# Ollama (local model)
uv run lumen run /path/to/repo --provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1

# OpenAI
uv run lumen run /path/to/repo --provider openai --model gpt-4o
```

Equivalent `make` wrapper:

```bash
make run REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'
make run REPO=/path/to/repo ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'
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

```
lumen mcp [REPO_PATH] [OPTIONS]

  --db-path TEXT         Serve an existing Kuzu DB directly
  --repo-path TEXT       Optional source repo path when serving an existing DB
  --repo-name TEXT       Override repo name in output dir
  --output-dir TEXT      Output directory (default: ./codedoc-output)
  --timeout INT          Per-stage timeout in seconds (default: 300)
  --repo-size-check      off | warn | strict (default: warn)
  --print-config         Print MCP client config snippets and exit
  --verbose              Stream logs as the pipeline runs
```

```
lumen mcp-http [REPO_PATH] [OPTIONS]

  --db-path TEXT         Serve an existing Kuzu DB directly
  --repo-path TEXT       Optional source repo path when serving an existing DB
  --repo-name TEXT       Override repo name in output dir
  --output-dir TEXT      Output directory (default: ./codedoc-output)
  --timeout INT          Per-stage timeout in seconds (default: 300)
  --repo-size-check      off | warn | strict (default: warn)
  --host TEXT            HTTP bind host (default: 127.0.0.1)
  --port INT             HTTP bind port (default: 8765)
  --path TEXT            HTTP MCP path (default: /mcp)
  --print-config         Print MCP client config snippets and exit
  --verbose              Stream logs as the pipeline runs
```

Docker convenience:

```bash
make docker-pipeline REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'
make docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
make docker-docs
```
Both `make mcp-http` and `make docker-mcp` already print the config before starting the server.
Use `--print-config` only when you want config output without keeping the MCP server running.

---

## Output structure

```
output/<repo-name>-<timestamp>/
├── pipeline.json          ← run metadata, repo metrics, and token usage
├── index.kuzu             ← Code Property Graph database (KuzuDB)
└── artifacts/             ← documentation artifacts
    ├── domain/
    │   ├── business-capabilities.md  ← capabilities + business rules per capability
    │   └── er-diagram.md             ← Mermaid ER diagram (backend only)
    ├── architecture/
    │   ├── business-journeys.md      ← Mermaid sequence diagrams for key flows
    │   └── c4-context.md             ← deterministic PlantUML C4Context (upstream + downstream)
    ├── tech/
    │   └── coupling-hotspots.md      ← hotspot table + dead code + seam candidates
    ├── current-state/
    │   └── api-spec.yaml             ← OpenAPI spec (backend only, conditional)
    ├── target-state/
    │   ├── bounded-contexts.md       ← BC decomposition + service table
    │   └── strangler-fig.md          ← ordered extraction plan
    └── manifests/artifacts.json

output/doc-site/               ← shared MkDocs Material site (accumulates all runs)
```

---

## Configuration

`pipeline/.codedoc.toml` sets persistent defaults (CLI flags always win):

Runtime defaults are repo-size aware:

- `small` / `medium`: `timeout = 300`, `max_turns = 60`
- `large` / `xlarge`: `timeout = 3600`
- full docs pipeline only: `large` / `xlarge` also use `max_turns = 100`

Explicit CLI flags or `.codedoc.toml` values still override those adaptive defaults.
If you pin `timeout` or `max_turns` in `.codedoc.toml`, that setting will no longer adapt by repo size.

```toml
[pipeline]
model     = "claude-sonnet-4-6"
provider  = "auto"
repo_size_check = "warn"
allow_xlarge = false

[paths]
output_dir = "./my-output"
```

---

## Cost

Run cost depends on:

- provider pricing
- model choice
- the input/output token split
- repo size and complexity
- whether you are doing a first full run or reusing an indexed graph over MCP

For Sonnet-class pricing, output tokens are materially more expensive than input tokens, so
a flat "total tokens = cost" estimate is often misleading.

Example from a recent run:

- `311,287` input tokens
- `28,098` output tokens
- `339,385` total tokens
- roughly `$1.36` at Sonnet 4 API pricing

The main cost advantage of lumen is not that every full run is always cheaper. It is that
the graph-first architecture scales better than naive full-repo prompting on medium and
large repos, and that follow-up analysis becomes cheaper once the same indexed repo is
reused through MCP.

Exact token usage is recorded in `pipeline.json` after every run.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
