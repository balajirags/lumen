.PHONY: install install-indexer install-pipeline run mcp mcp-http dev-docs dev-ui test \
        docker-build docker-run docker-pipeline docker-mcp compose-docs compose-ui

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

dev-docs:
	bash pipeline/scripts/build-docs-site.sh --output-dir output --site-dir output/doc-site
	cd pipeline && uv run python -m http.server 8081 --directory ../output/doc-site

dev-ui:
	cd ui && npm install && npm run dev

test:
	cd pipeline && python tests/_test_kuzu.py

# ── Docker ──
DOCKER_IMAGE ?= lumen

docker-build:
	docker build -t $(DOCKER_IMAGE) .
	docker image prune -f

docker-rebuild:
	docker build --no-cache -t $(DOCKER_IMAGE) .
	docker image prune -f

docker-run:
	@if [ -z "$(REPO)" ]; then echo "Usage: make docker-run REPO=/path/to/repo"; exit 1; fi
	docker run --rm \
	  -v "$(REPO)":/repo \
	  -v "$(PWD)/output":/workspace/output \
	  -e ANTHROPIC_API_KEY="$(ANTHROPIC_API_KEY)" \
	  $(DOCKER_IMAGE) run /repo --repo-name "$(notdir $(REPO))" --output-dir /workspace/output

# ── Docker Compose ──
docker-pipeline:
	@if [ -z "$(REPO)" ]; then echo "Usage: make docker-pipeline REPO=/path/to/repo [ARGS='--provider ollama ...']"; exit 1; fi
	REPO_PATH="$(REPO)" OUTPUT_PATH="$(PWD)/output" COMPOSE_PROFILES=pipeline \
	  docker-compose run pipeline -- \
	  run /repo --repo-name "$(notdir $(REPO))" --output-dir /workspace/output $(ARGS)

docker-mcp:
	@if [ -z "$(REPO)" ]; then echo "Usage: make docker-mcp REPO=/path/to/repo [ARGS='--repo-size-check warn'] [PORT=8765]"; exit 1; fi
	REPO_PATH="$(REPO)" REPO_NAME="$(notdir $(REPO))" OUTPUT_PATH="$(PWD)/output" MCP_PORT="$${PORT:-8765}" COMPOSE_PROFILES=mcp \
	  docker-compose run --service-ports --rm mcp-http $(ARGS)

compose-docs:
	OUTPUT_PATH="$(PWD)/output" COMPOSE_PROFILES=docs docker-compose up docs

compose-ui:
	OUTPUT_PATH="$(PWD)/output" COMPOSE_PROFILES=ui docker-compose up ui
