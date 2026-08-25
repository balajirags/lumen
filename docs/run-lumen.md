# Run Lumen Offline

This guide explains how to run Lumen from a release bundle on a machine where pulling images from a registry is not available.

---

## Docker bundle

### 1. Untar and load

```bash
tar -xzf lumen-docker-<version>-<arch>.tar.gz
cd lumen-docker-<version>-<arch>/runtime

./scripts/lumen-docker-load.sh ../images/lumen-<version>-<arch>.tar
```

Verify the image loaded:

```bash
docker images | grep lumen
```

### 2. Run the full pipeline

```bash
export ANTHROPIC_API_KEY=...
# or: export OPENAI_API_KEY=...

make lumen-docker-run REPO=/path/to/repo \
  ARGS='--provider anthropic --model claude-sonnet-4-6'
```

For a local Ollama model:

```bash
make lumen-docker-run REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"
```

### 3. MCP mode

Serve an existing DB:

```bash
make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
```

Or index a repo and serve MCP directly:

```bash
make lumen-docker-mcp REPO=/path/to/repo
```

MCP endpoint: `http://127.0.0.1:8765/mcp`

### 4. Serve generated docs

```bash
make lumen-docker-docs    # → http://localhost:8081
```

---

## Native bundle

### 1. Untar and install

```bash
tar -xzf lumen-<version>-<os>-<arch>.tar.gz
cd lumen-<version>-<os>-<arch>

./install.sh          # symlinks lumen into ~/.local/bin
```

Make sure `~/.local/bin` is on your PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Optionally verify bundle integrity before installing:

```bash
./verify.sh
```

### 2. Prerequisites

Only `graphviz` is required — no Docker, no Java, no Node, no Python needed.

```bash
# macOS
brew install graphviz

# Linux
sudo apt install graphviz   # or: sudo yum install graphviz
```

### 3. Run

```bash
lumen run /path/to/repo --provider anthropic --model claude-sonnet-4-6
lumen run /path/to/repo --provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1

# MCP mode
lumen mcp /path/to/repo
```

---

## Outputs

- Pipeline output: `./lumen-output/` (relative to where `lumen` was invoked)
- Docker pipeline output: `./output/` (relative to where `make` was run)

## Troubleshooting

- **Docker image not found**: confirm `docker images` shows `lumen` after running `lumen-docker-load.sh`
- **Architecture mismatch**: use the bundle that matches your machine — `arm64` for Apple Silicon, `amd64` for Intel/AMD
- **Ollama on host**: use `host.docker.internal` as the hostname in `--base-url`
- **API key missing**: set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` before running `make`
- **graphviz missing** (native): docs generation requires `graphviz`; install via brew/apt
