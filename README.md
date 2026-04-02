# lumen

**Illuminate any codebase.** Point lumen at a repository and it produces a full documentation site — architecture diagrams, API inventory, domain model, migration roadmap — using a knowledge graph and LLMs.

```
Source repo → knowledge graph → LLM analysis → documentation site
```

## What you get

After a single command, lumen writes a Docusaurus site with:

| Document | What it covers |
|---|---|
| API & Module Inventory | Every endpoint, package, and domain entity |
| System Architecture | Layered architecture, coupling hotspots, data flow, design patterns |
| Domain Analysis | Business capabilities, bounded contexts, domain events |
| Migration Roadmap | Risk matrix, dead code, modernization phases (no fabricated timelines) |
| Target State Blueprint | Target microservice map and migration principles |

---

## Quickstart (Docker — no local prerequisites)

```bash
docker pull ghcr.io/your-org/lumen   # or: docker build -t lumen .

docker run --rm \
  -v /path/to/repo:/repo \
  -v $(pwd)/output:/workspace/output \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  lumen run /repo --output-dir /workspace/output
```

Or with the Makefile:

```bash
make docker-build
make docker-run REPO=/path/to/repo
```

### Using a local Ollama model

Inside Docker, `localhost` refers to the container — not your machine. Use
`host.docker.internal` to reach your host:

```bash
# Mac / Windows (Docker Desktop): host.docker.internal is automatic
# Linux: docker-compose.yml already adds extra_hosts for you

make compose-pipeline REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"
```

---

## Viewing results

### Doc-site (generated Docusaurus documentation)

After a pipeline run, serve the docs at `http://localhost:8080`:

```bash
make compose-docs
```

The site is served from `./output/` on your host — no rebuild needed.

### Graph UI (visual KuzuDB explorer)

Start the graph visualization UI at `http://localhost:3001`:

```bash
make compose-ui
```

Then connect it to a database from a previous run:
- DB type: **KuzuDB**
- DB path: `/data/<repo-name>-<timestamp>/index.kuzu/db`

The `/data` path inside the container maps to `./output/` on your host.

---

## Native install (optional — requires Java 21, Node 18, Python 3.11+)

### Prerequisites

| Requirement | Purpose |
|---|---|
| Python 3.11+ | Pipeline runtime |
| Java 21+ | Indexing Java / Kotlin repos |
| Node 18+ | Indexing JavaScript / TypeScript repos; graph UI |
| An LLM API key | Analysis (Anthropic Claude by default) |

---

## Installation

```bash
git clone <repo-url> lumen
cd lumen

# Build indexer runtimes + install CLI
make install
```

Copy the example env file and add your API key:

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY
```

---

## Usage

```bash
cd pipeline
lumen run /path/to/your/repo
```

With options:

```bash
lumen run /path/to/repo \
  --verbose \
  --model claude-sonnet-4-6 \
  --output-dir ./my-output
```

Using a local model (Ollama):

```bash
lumen run /path/to/repo \
  --provider ollama \
  --model qwen2.5:32b \
  --base-url http://localhost:11434/v1
```

### All options

```
Options:
  --model TEXT        LLM model to use (default: claude-sonnet-4-6)
  --provider TEXT     LLM provider: auto, anthropic, ollama, openai (default: auto)
  --base-url TEXT     Override API endpoint (for Ollama or custom OpenAI-compatible servers)
  --output-dir TEXT   Where to write output (default: ./codedoc-output)
  --max-turns INT     Max LLM turns per phase (default: 40)
  --verbose           Stream logs as the pipeline runs
  --help              Show this message and exit
```

---

## Output

Each run produces an output directory:

```
codedoc-output/<repo-name>-<timestamp>/
├── pipeline.json          ← run metadata and token usage
├── index.kuzu/            ← knowledge graph database
├── artifacts/             ← the 7 markdown documents
│   ├── current-state/inventory.md
│   ├── architecture/system-overview.md
│   ├── domain/domain-analysis.md
│   ├── migration/roadmap.md
│   ├── target-state/blueprint.md
│   └── manifests/artifacts.json
└── doc-site/              ← built Docusaurus site
```

Open the docs site:

```bash
cd doc-site && npm start
```

---

## Configuration file

Create `pipeline/.codedoc.toml` to set persistent defaults:

```toml
[pipeline]
model     = "claude-sonnet-4-6"
max_turns = 40

[paths]
output_dir = "./my-output"
```

CLI flags always override this file.

---

## Graph UI (optional)

lumen includes a visual graph explorer for browsing the knowledge graph produced by the indexer.

```bash
make dev-ui
```

Opens at `http://localhost:5173`. Connect it to any `.kuzu` database from a previous run.

---

## Supported languages

| Language | Indexer |
|---|---|
| Java / Kotlin | `cmg-java` (fat JAR, Java 21) |
| JavaScript / TypeScript | `cmg-js` (Node + Babel) |
| Python | `cmg-python` (AST parser) |

---

## Cost

A typical run on a medium-sized repo (~50k lines) uses roughly 200k–400k tokens.
At Claude Sonnet pricing that is approximately **$0.10–$0.25 per run**.

Token usage is recorded in `pipeline.json` after every run.
