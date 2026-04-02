# lumen

**Illuminate any codebase.** Point lumen at a repository and it produces a full documentation site — architecture diagrams, API inventory, domain model, migration roadmap — using a knowledge graph and LLMs.

```
Source repo → knowledge graph → LLM analysis → documentation site
                            ↘ graph UI (visual explorer)
```

## What you get

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
git clone <repo-url> lumen && cd lumen
make docker-build
```

Set your API key:

```bash
cp .env.example .env
# edit .env: ANTHROPIC_API_KEY=sk-ant-...
```

Run:

```bash
make docker-run REPO=/path/to/your/repo
```

### Using a local Ollama model

Inside Docker, `localhost` refers to the container — not your machine. Use
`host.docker.internal` to reach your host's Ollama:

```bash
# Mac / Windows (Docker Desktop): host.docker.internal is automatic
# Linux: docker-compose.yml already configures extra_hosts for you

make compose-pipeline REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"
```

---

## Viewing results

After a pipeline run, output lands in `./output/`.

### Doc-site

Browse the generated Docusaurus documentation at **http://localhost:8080**:

```bash
make compose-docs
```

### Graph UI

Explore the knowledge graph visually at **http://localhost:3001**:

```bash
make compose-ui
```

Connect it to a database from a previous run:
- DB type: **KuzuDB**
- DB path: `/data/<repo-name>-<timestamp>/index.kuzu/db`

(`/data` inside the container maps to `./output/` on your host.)

---

## Native install (optional)

Requires Java 21, Node 18, Python 3.11+.

```bash
cp .env.example .env        # add ANTHROPIC_API_KEY
make install                # builds indexer runtimes + installs Python CLI
```

Run:

```bash
cd pipeline
lumen run /path/to/repo --verbose
```

With a local Ollama model:

```bash
lumen run /path/to/repo \
  --provider ollama \
  --model qwen2.5:32b \
  --base-url http://localhost:11434/v1
```

Open the graph UI in dev mode:

```bash
make dev-ui    # Vite → http://localhost:5173  |  Express → http://localhost:3001
```

---

## All CLI options

```
lumen run REPO_PATH [OPTIONS]

  --model TEXT        LLM model (default: claude-sonnet-4-6)
  --provider TEXT     auto | anthropic | ollama | openai  (default: auto)
  --base-url TEXT     Override API endpoint (e.g. http://localhost:11434/v1)
  --output-dir TEXT   Output directory (default: ./codedoc-output)
  --max-turns INT     Max LLM turns per phase (default: 40)
  --verbose           Stream logs as the pipeline runs
  --help
```

---

## Output structure

```
codedoc-output/<repo-name>-<timestamp>/
├── pipeline.json              ← run metadata and token usage
├── index.kuzu/                ← knowledge graph database
├── artifacts/                 ← 7 markdown documents
│   ├── current-state/inventory.md
│   ├── architecture/system-overview.md
│   ├── domain/domain-analysis.md
│   ├── migration/roadmap.md
│   ├── target-state/blueprint.md
│   └── manifests/artifacts.json
└── doc-site/                  ← built Docusaurus site
```

---

## Configuration file

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
| Java / Kotlin | `cmg-java` (fat JAR, Java 21) |
| JavaScript / TypeScript | `cmg-js` (Node + Babel) |
| Python | `cmg-python` (AST parser) |

---

## Cost

A typical run on a medium-sized repo (~50k lines) uses roughly 200k–400k tokens.
At Claude Sonnet pricing that is approximately **$0.10–$0.25 per run**.

Token usage is recorded in `pipeline.json` after every run.
