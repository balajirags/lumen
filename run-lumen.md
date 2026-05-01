# Run Lumen Offline

This guide explains how to run Lumen on a laptop or VM where pulling images from a central registry is not allowed.

## 1. Use a release bundle

If you received a release candidate, unpack it first:

```bash
tar -xzf lumen-rc-<version>-<arch>.tar.gz
cd lumen-rc-<version>-<arch>/runtime
```

The unpacked bundle includes:

- `../images/<image>.tar`
- `Makefile`
- `run-lumen.md`
- `docker-source.md`
- `scripts/lumen-docker-load.sh`
- `scripts/lumen-docker-run.sh`
- `scripts/lumen-docker-mcp.sh`
- `scripts/lumen-docker-docs.sh`
- `release.txt`
- `../checksums/SHA256SUMS`

Optional checksum verification:

```bash
cd ..
shasum -a 256 -c checksums/SHA256SUMS
cd runtime
```

## 2. Load the image

```bash
./scripts/lumen-docker-load.sh ../images/lumen-0.1.0-amd64.tar
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

make lumen-docker-run REPO=/path/to/repo \
  ARGS='--provider anthropic --model claude-sonnet-4-6'
```

For a local Ollama model:

```bash
make lumen-docker-run REPO=/path/to/repo \
  ARGS="--provider ollama --model qwen2.5:32b --base-url http://host.docker.internal:11434/v1"
```

## 5. Run MCP mode

Use an existing DB:

```bash
make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db
```

Or index a repo and serve MCP directly:

```bash
make lumen-docker-mcp REPO=/path/to/repo
```

Default MCP URL:

- `http://127.0.0.1:8765/mcp`

## 6. Serve generated docs

```bash
make lumen-docker-docs
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
