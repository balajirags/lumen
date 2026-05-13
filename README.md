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
[Multi-agent pipeline] — researcher fan-out + architect
    │
    ├─ Phase 1: Orientation (direct graph call — no LLM)
    │
    ├─ Phase 2: 3 parallel researchers (graph queries + write artifacts)
    │   ├─ Domain researcher   → business capabilities + ER diagram
    │   ├─ Flows researcher    → journeys + C4 context + UI/API interaction views + API spec
    │   └─ Tech researcher     → coupling hotspots + module/dependency signals
    │
    ├─ Phase 3: Synthesis / recovery
    │          deterministic backfill + targeted recovery for missing required artifacts
    │
    └─ Phase 4: Architect + summary
               → target-state plan + executive summary + manifest
    │
    ▼
[Builder] → MkDocs Material documentation site (Mermaid + deterministic C4 PlantUML for C1 context views)
         → KuzuDB graph for MCP-backed exploration
```

### Supported languages

lumen currently supports:

- Java / Kotlin
- JavaScript / TypeScript (JSX, TSX, ES modules)
- Python

Multi-language repos are indexed in one run when supported language slices are present.

**Android repos are detected early and rejected** — `AndroidManifest.xml` triggers an informative error before indexing starts, since Android SDK framework classes are not available for resolution. Use `--language kotlin` to force indexing a specific module directly.

### Core USP

The main differentiators are:

1. **Token-aware architecture** — lumen minimizes raw source-file stuffing by default, using graph queries and targeted source reads instead. This is most beneficial on medium, large, and repeatedly queried repos.
2. **Code Property Graph over files or plain AST** — lumen indexes the repo into a graph with structural and relationship-aware facts instead of relying on file-by-file reading or a plain syntax tree.
3. **Post-processing abstractions** — the graph layer derives higher-level nodes that analysts use directly: **Workflow** nodes (pre-computed end-to-end execution traces from HTTP entry points to repository/event terminals) and **Domain** nodes (functional clusters of cohesive classes with cohesion scores). These are embedded into the agent orientation summary so analysts start with architectural context already available.
4. **Multi-agent analysis** — lumen uses parallel analysts plus a synthesizing architect, closer to stochastic consensus / distributed research than a single monolithic agent pass.
5. **Model flexibility** — lumen can run with local Ollama models as well as OpenAI and Anthropic models.

In practice, that gives you:

- better token and cost scaling than naive full-repo prompting on medium and large repos
- lower follow-up analysis cost once a repo has been indexed and reused through MCP
- better structural reasoning on medium and large repos
- stronger synthesis through parallel graph-driven investigation
- local or hosted model choice depending on cost, privacy, and quality needs

### Why it is different

- **Multi-language indexing per run** — supported Java/Kotlin, JS/TS, and Python slices are all indexed in the same run when present.
- **Normalized graph metadata** — nodes and edges carry `language`, `kind`, and `normKind` so tooling can reason across language-specific parser outputs more consistently.
- **Three-tier CALL confidence** — CALLS edges carry `confidence` (0.95 same-file / 0.90 import-resolved / 0.50 global) and `reason` so downstream analysis can filter to reliable edges only.
- **Workflow and Domain post-processing** — the indexer derives Workflow nodes (HTTP entry → repository/event terminal) and Domain nodes (functional clusters with cohesion scores) as a post-processing pass, then pre-fetches them into agent orientation for immediate use.
- **Repo metrics guardrail** — a native preflight plugin estimates repo size using LOC, source-file count, and language mix before indexing starts.
- **Repo-type-aware prompting** — the pipeline classifies a repo once, then carries `primary_repo_type`, capabilities, and an artifact plan through later stages.
- **Artifact path enforcement** — `write_artifact` only accepts paths listed in the run's artifact plan; spurious or wrong-path writes are rejected with a helpful correction message.
- **Context-aware pruning** — the agent loop removes older turns in proportion to budget overage (3 turns at once for large/xlarge repos); critical endpoint evidence is pre-fetched and pinned before the loop starts so it survives pruning.
- **Frontend-aware JS/TS analysis** — React/TSX repos get graph-backed component, hook, and UI-to-API exploration. Route-map and component-boundaries artifacts are suppressed for JS-frontend repos where call graph evidence is too sparse to produce reliable output.
- **Improved CLI UX** — indexing shows live per-language progress, the three parallel researchers render as distinct colored live boxes, and a per-researcher tool usage table (highlighting new aggregate tools) prints after each phase.
- **MCP access** — `lumen mcp` serves the indexed graph over HTTP so other tools and clients can query the repo without rerunning the full docs pipeline.
- **Split pipeline modules** — the full docs flow and the MCP flow live in separate pipeline modules with shared setup/finalization helpers.

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
| Executive Summary | CXO-facing summary of the repo’s purpose, current state, risks, recommendations, and confidence limits |
| Business Capabilities | All capabilities in the system + business rules and validations per capability. Grounded in pre-computed Domain clusters when available. |
| Business Journeys | Key user or integration flows as "As a [role], I can [action]…" + Mermaid sequence diagrams. Grounded in pre-computed Workflow step traces when available. |
| C4 System Context | Integration map — upstream callers + downstream dependencies + protocols (deterministic PlantUML) |
| Coupling Hotspots | Risk matrix, coupling pairs, dead code candidates, decomposition seam candidates |
| UI to API Interactions | Which UI routes/components/hooks call which API clients and backend endpoints |
| ER Diagram | Entity relationships and bounded context ownership (Mermaid). For Java/Spring repos, derived from JPA annotations and class field edges. For JS/TS repos, derived from TypeScript class/interface properties and module-path/naming conventions. |
| API Spec | OpenAPI YAML (required for backend/fullstack repos; conditional for large/xlarge) |
| Bounded Contexts | Bounded context decomposition grounded in coupling + domain evidence |
| Strangler Fig Plan | Ordered extraction plan with seam identification and routing strategy |
| Repo Metrics | Preflight LOC / file-count / language-mix assessment with size/risk classification |

## Getting Started

Set your LLM provider credentials:

```bash
export ANTHROPIC_API_KEY=...   # or: export OPENAI_API_KEY=...
```

lumen can be installed and run in three ways. Pick the one that fits your setup.

---

### Mode 1: Docker (recommended)

No local toolchain required — just Docker.

> **Docker runtime resources**
> The indexer and KuzuDB are memory-intensive. Configure your Docker runtime with at least
> **4 CPUs and 8 GB RAM** before running lumen.
>
> - **Docker Desktop** (Mac/Windows): Settings → Resources → set CPUs ≥ 4, Memory ≥ 8 GB
> - **Colima**: `colima start --cpu 4 --memory 8`

#### Install

```bash
git clone <repo-url> lumen && cd lumen
make lumen-docker-build
```

#### Run the full pipeline

```bash
make lumen-docker-run REPO=/path/to/repo \
  ARGS='--provider anthropic --model claude-sonnet-4-6'
```

For local Ollama models, use `host.docker.internal` to reach the host network:

```bash
# Mac/Windows: host.docker.internal is automatic
# Linux: the wrapper scripts add host.docker.internal:host-gateway
make lumen-docker-run REPO=/path/to/repo \
  ARGS='--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1'
```

#### MCP mode

Serve an existing Kuzu DB over HTTP MCP:

```bash
make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
```

Or index a new repo and serve it directly:

```bash
make lumen-docker-mcp REPO=/path/to/repo
```

MCP is exposed at `http://127.0.0.1:8765/mcp`. Connect any MCP-capable client to that URL.

#### View docs

```bash
make lumen-docker-docs    # → http://localhost:8081
```

Rebuilds the MkDocs site from the existing `./output` directory — no pipeline rerun needed.
The doc-site **accumulates** across runs, so every analysed repo appears as a tab.

#### xlarge repos

If preflight classifies the repo as `xlarge`, `lumen-docker-run` stops before indexing and
directs you to MCP mode:

1. Run `make lumen-docker-run ...` as usual.
2. If lumen stops after preflight, do not rerun `lumen-docker-run`.
3. Start MCP mode: `make lumen-docker-mcp REPO=/path/to/your/repo`.
4. Connect your LLM client to `http://127.0.0.1:8765/mcp` and ask focused questions.

To force the full docs pipeline on an xlarge repo:

```bash
make lumen-docker-run REPO=/path/to/repo \
  ARGS='--allow-xlarge --provider anthropic --model claude-sonnet-4-6'
```

#### Release candidate bundle

```bash
TAG=v0.1.0 scripts/lumen-docker-release.sh
```

Packages the local `lumen:latest` Docker image into `releases/`. If the image is missing,
it runs `make lumen-docker-build` first. `TAG` is used for bundle naming only — create
and push git tags manually.

---

### Mode 2: Build Locally (Developer)

Clone the repo and build everything from source. Requires Java 21, Node 20, Python 3.11+,
and [uv](https://docs.astral.sh/uv/).

#### Install

```bash
git clone <repo-url> lumen && cd lumen
make lumen-install          # builds indexer (Java fat JAR + parsers) + installs pipeline via uv
```

This runs `indexer/install.sh` (Gradle shadowJar, npm ci, parser wrappers) and `uv sync`
in `pipeline/`. Re-run after any indexer code change.

#### Run the full pipeline

```bash
make lumen-run REPO=/path/to/repo \
  ARGS='--provider anthropic --model claude-sonnet-4-6'
```

Or invoke `lumen` directly via uv:

```bash
cd pipeline
uv run lumen run /path/to/repo --provider anthropic --model claude-sonnet-4-6
uv run lumen run /path/to/repo --provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1
uv run lumen run /path/to/repo --provider openai --model gpt-4o
```

#### MCP mode

```bash
make lumen-mcp REPO=/path/to/repo
# or with an existing DB:
make lumen-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
```

#### View docs

```bash
make lumen-docker-docs    # → http://localhost:8081 (uses Docker for serving)
```

---

## CLI Reference

```
lumen run REPO_PATH [OPTIONS]

  --provider TEXT     auto | anthropic | ollama | openai
  --model TEXT        LLM model (default: claude-sonnet-4-6)
  --base-url TEXT     Override API endpoint (e.g. http://localhost:11434/v1)
  --repo-name TEXT    Override repo name in output dir (useful when mounted at /repo)
  --output-dir TEXT   Output directory (default: ./codedoc-output)
  --max-turns INT     Max LLM turns per phase (default: 60)
  --repo-size-check   off | warn | strict (default: warn)
  --allow-xlarge      Force full pipeline on xlarge repos
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

---

## Output structure

```
output/<repo-name>-<timestamp>/
├── pipeline.json          ← run metadata, repo metrics, runtime model/provider, and token usage
├── index.kuzu             ← Code Property Graph database (KuzuDB)
└── artifacts/             ← documentation artifacts
    ├── summary/
    │   └── executive-summary.md     ← business-facing summary, risks, recommendations, confidence
    ├── domain/
    │   ├── business-capabilities.md  ← capabilities + business rules per capability
    │   └── er-diagram.md             ← Mermaid ER diagram (required for backend/fullstack repos)
    ├── architecture/
    │   ├── user-journeys.md          ← Mermaid sequence diagrams for key flows
    │   ├── c4-context.md             ← deterministic PlantUML C4Context (upstream + downstream)
    │   └── route-map.md              ← UI route inventory or SPA entry-surface fallback
    ├── tech/
    │   └── coupling-hotspots.md      ← hotspot table + dead code + seam candidates
    ├── current-state/
    │   ├── api-spec.yaml             ← OpenAPI spec (required for backend/fullstack repos)
    │   ├── ui-to-api-interactions.md ← UI/component to API/client interaction view, with import-fallback when direct call edges are weak
    │   └── module-dependency-map.md  ← dependency and seam summary
    ├── target-state/
    │   ├── bounded-contexts.md       ← BC decomposition + service table (backend-service)
    │   ├── strangler-fig.md          ← ordered extraction plan (backend-service)
    │   ├── fullstack-boundaries.md   ← frontend/backend seam plan (fullstack-app)
    │   └── migration-plan.md         ← target-state migration plan
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

Indicative ranges across repo sizes (Sonnet pricing, input $3/M, output $15/M):

| Size band | LOC | Type | Token range | Sonnet est. |
|---|---|---|---|---|
| small | 1k–10k | backend | 150k–380k | $0.53–$1.35 |
| small | 1k–10k | fullstack | 350k–800k | $1.24–$2.84 |
| large | 50k–200k | backend | 600k–1,100k | $2.13–$3.91 |
| xlarge | >200k | backend | 800k–1,400k | $2.84–$4.97 |

Output tokens are consistently ~4–5% of total. Exact token usage is recorded in `pipeline.json` after every run.

The main cost advantage of lumen is not that every full run is always cheaper. It is that
the graph-first architecture scales better than naive full-repo prompting on medium and
large repos, and that follow-up analysis becomes cheaper once the same indexed repo is
reused through MCP.

Exact token usage is recorded in `pipeline.json` after every run.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
