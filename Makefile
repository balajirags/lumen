.PHONY: help docs install install-indexer install-pipeline run mcp mcp-http dev-ui test \
        docker-build docker-rebuild docker-pipeline docker-mcp docker-docs compose-ui


# ── Docker ──
DOCKER_IMAGE ?= lumen
DOCKER_UI_IMAGE ?= lumen-ui

docker-build:
	docker build -t $(DOCKER_IMAGE) .
	docker image prune -f

docker-rebuild:
	docker build --no-cache -t $(DOCKER_IMAGE) .
	docker image prune -f

# ── Docker Compose ──
docker-pipeline:
	@if [ -z "$(REPO)" ]; then echo "Usage: make docker-pipeline REPO=/path/to/repo [ARGS='--provider ollama ...']"; exit 1; fi
	REPO_PATH="$(REPO)" OUTPUT_PATH="$(PWD)/output" DOCKER_IMAGE="$(DOCKER_IMAGE)" COMPOSE_PROFILES=pipeline \
	  docker-compose run pipeline -- \
	  run /repo --repo-name "$(notdir $(REPO))" --output-dir /workspace/output $(ARGS)

docker-mcp:
	@if [ -z "$(DB)" ] && [ -z "$(REPO)" ]; then \
	  echo "Usage: make docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "   or: make docker-mcp REPO=/path/to/repo"; \
	  echo "PORT defaults to 8765."; \
	  exit 1; fi
	@REPO_NAME_VALUE=""; \
	if [ -n "$(REPO)" ]; then REPO_NAME_VALUE="$(notdir $(REPO))"; fi; \
	REPO_PATH="$(REPO)" REPO_NAME="$$REPO_NAME_VALUE" DB_PATH="$(DB)" OUTPUT_PATH="$(PWD)/output" DOCKER_IMAGE="$(DOCKER_IMAGE)" MCP_PORT="$${PORT:-8765}" COMPOSE_PROFILES=mcp \
	  docker-compose run --service-ports --rm mcp-http

docker-docs:
	OUTPUT_PATH="$(PWD)/output" DOCKER_IMAGE="$(DOCKER_IMAGE)" DOCS_PORT="$${PORT:-8081}" COMPOSE_PROFILES=docs docker-compose up docs

compose-ui:
	OUTPUT_PATH="$(PWD)/output" DOCKER_UI_IMAGE="$(DOCKER_UI_IMAGE)" COMPOSE_PROFILES=ui docker-compose up ui


# ── Native install (requires Java 21, Node 18, Python 3.11) ──
install: install-indexer install-pipeline

install-indexer:
	cd indexer && bash install.sh

install-pipeline:
	cd pipeline && uv sync

run:
	@if [ -z "$(REPO)" ]; then \
	  echo "Usage: make run REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
	  echo "  Anthropic: ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	  echo "  Ollama:    ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'"; \
	  echo "  OpenAI:    ARGS='--provider openai --model gpt-4o'"; \
	  exit 1; fi
	@if [ -z "$(ARGS)" ]; then \
	  echo "ERROR: ARGS is required. Pass --provider, --model, and optionally --base-url."; \
	  echo "  Anthropic: ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	  echo "  Ollama:    ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'"; \
	  exit 1; fi
	cd pipeline && uv run lumen run "$(REPO)" --output-dir "$(PWD)/output" $(ARGS)

mcp:
	@if [ -z "$(REPO)" ]; then \
	  echo "Usage: make mcp REPO=/path/to/repo [ARGS='--timeout 1800 --repo-size-check warn']"; \
	  exit 1; fi
	cd pipeline && uv run lumen mcp "$(REPO)" --repo-name "$(notdir $(REPO))" --output-dir "$(PWD)/output" $(ARGS)

mcp-http:
	@if [ -z "$(REPO)" ]; then \
	  echo "Usage: make mcp-http REPO=/path/to/repo [ARGS='--port 8765 --repo-size-check warn']"; \
	  exit 1; fi
	cd pipeline && uv run lumen mcp-http "$(REPO)" --repo-name "$(notdir $(REPO))" --output-dir "$(PWD)/output" $(ARGS)

dev-ui:
	cd ui && npm install && npm run dev

test:
	cd pipeline && python tests/_test_kuzu.py

help:
	@if [ -n "$(CMD)" ]; then \
	  case "$(CMD)" in \
	    docker-pipeline) \
	      echo "make docker-pipeline"; \
	      echo ""; \
	      echo "Run preflight + index + multi-agent analysis + docs build in Docker."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  ARGS='--provider <p> --model <m> [--base-url <url>] [other lumen run flags]'"; \
	      echo "  DOCKER_IMAGE=lumen"; \
	      echo ""; \
	      echo "Example:"; \
	      echo "  make docker-pipeline REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	      ;; \
	    docker-mcp) \
	      echo "make docker-mcp"; \
	      echo ""; \
	      echo "Serve HTTP MCP in Docker."; \
	      echo ""; \
	      echo "Provide exactly one of:"; \
	      echo "  DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	      echo "  REPO=/path/to/repo"; \
	      echo ""; \
	      echo "Behavior:"; \
	      echo "  DB=...   serve an existing indexed DB"; \
	      echo "  REPO=... run preflight + index + MCP"; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  PORT=8765"; \
	      echo "  DOCKER_IMAGE=lumen"; \
	      echo ""; \
	      echo "Examples:"; \
	      echo "  make docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	      echo "  make docker-mcp REPO=/path/to/repo PORT=9000"; \
	      ;; \
	    docker-docs) \
	      echo "make docker-docs"; \
	      echo ""; \
	      echo "Rebuild and serve docs from ./output using the lumen Docker image. This is the default docs viewer."; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  PORT=8081"; \
	      echo "  DOCKER_IMAGE=lumen"; \
	      ;; \
	    run) \
	      echo "make run"; \
	      echo ""; \
	      echo "Run the full Lumen pipeline natively."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo "  ARGS='--provider <p> --model <m> [--base-url <url>] [other lumen run flags]'"; \
	      ;; \
	    mcp) \
	      echo "make mcp"; \
	      echo ""; \
	      echo "Run stdio MCP natively."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  ARGS='--timeout 1800 --repo-size-check warn'"; \
	      ;; \
	    mcp-http) \
	      echo "make mcp-http"; \
	      echo ""; \
	      echo "Run HTTP MCP natively."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  ARGS='--port 8765 --repo-size-check warn'"; \
	      ;; \
	    *) \
	      echo "Unknown CMD: $(CMD)"; \
	      echo "Use: make help"; \
	      exit 1; \
	      ;; \
	  esac; \
	else \
	  echo "Lumen Make Targets"; \
	  echo ""; \
	  echo "Docker-first:"; \
	  echo "  make docker-build"; \
	  echo "    Build the reusable '$(DOCKER_IMAGE)' image."; \
	  echo "  make docker-pipeline REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
	  echo "    Run preflight + index + multi-agent analysis + docs build in Docker."; \
	  echo "  make docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "    Serve an existing indexed DB over HTTP MCP in Docker."; \
	  echo "  make docker-mcp REPO=/path/to/repo"; \
	  echo "    Run preflight + index + HTTP MCP in Docker."; \
	  echo "  make docker-docs"; \
	  echo "    Rebuild and serve the generated MkDocs site from ./output in Docker. Recommended default."; \
	  echo ""; \
	  echo "Native:"; \
	  echo "  make install"; \
	  echo "    Build indexers and sync the Python pipeline environment."; \
	  echo "  make run REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
	  echo "    Run the full Lumen pipeline natively."; \
	  echo "  make mcp REPO=/path/to/repo"; \
	  echo "    Run stdio MCP natively."; \
	  echo "  make mcp-http REPO=/path/to/repo"; \
	  echo "    Run HTTP MCP natively."; \
	  echo ""; \
	  echo "Development:"; \
	  echo "  make docs        (alias for docker-docs)"; \
	  echo "  make dev-ui"; \
	  echo "  make compose-ui"; \
	  echo ""; \
	  echo "Common variables:"; \
	  echo "  REPO=/path/to/repo"; \
	  echo "  DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "  ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	  echo "  PORT=8765"; \
	  echo "  DOCKER_IMAGE=lumen"; \
	  echo "  DOCKER_UI_IMAGE=lumen-ui"; \
	  echo ""; \
	  echo "Per-command help:"; \
	  echo "  make help CMD=docker-mcp"; \
	  echo "  make help CMD=docker-pipeline"; \
	fi

docs: docker-docs
