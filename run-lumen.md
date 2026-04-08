# Run Lumen Offline

This guide explains how to run Lumen on a laptop or VM where pulling images from a central registry is not allowed.

## Prerequisites

- Docker installed and running
- Docker Compose available
- A prebuilt Lumen image archive such as `lumen-0.1.0-amd64.tar` or `lumen-0.1.0-arm64.tar`
- This runtime bundle, including `Makefile` and `docker-compose.yml`
- API credentials if using hosted providers such as Anthropic or OpenAI

Important:

- Image archives are architecture-specific.
- An `arm64` image must be loaded on an `arm64` machine.
- An `amd64` image must be loaded on an `amd64` machine.

## 1. Create the image tarball

Run these steps on a machine that is allowed to build the image:

```bash
make docker-build
docker save lumen:latest -o lumen-0.1.0-amd64.tar
```

Optional checksum:

```bash
shasum -a 256 lumen-0.1.0-amd64.tar
```

Transfer the tarball to the target laptop or VM using an approved offline method such as SCP, USB media, or an internal file share.

## 2. Load the image

```bash
docker load -i ../images/lumen-0.1.0-amd64.tar
```

## 3. Verify the image

```bash
docker images | grep lumen
```

Expected result:

- A local `lumen` image should appear in the image list.

## 4. Run the full pipeline

```bash
export ANTHROPIC_API_KEY=...
# or: export OPENAI_API_KEY=...

make docker-pipeline REPO=/path/to/repo \
  ARGS='--provider anthropic --model claude-sonnet-4-6'
```

For a local Ollama model:

```bash
make docker-pipeline REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"
```

## 5. Run MCP mode

Use an existing DB:

```bash
make docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
```

Or index a repo and serve MCP directly:

```bash
make docker-mcp REPO=/path/to/repo
```

Default MCP URL:

- `http://127.0.0.1:8765/mcp`

## 6. Serve generated docs

```bash
make docker-docs
```

Default docs URL:

- `http://localhost:8081`

## Outputs

- Generated output is written to `./output`
- Docs site is written under `./output/doc-site`

## Troubleshooting

- If `docker load` fails, confirm the archive matches the target machine architecture.
- If the image is not found, confirm `docker images` shows `lumen`.
- If Ollama is running on the host, use `host.docker.internal` as the Docker-side hostname.
- If hosted models are used, confirm the required API key environment variable is set before running `make`.
