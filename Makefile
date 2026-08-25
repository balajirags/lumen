.PHONY: lumen-help lumen-install lumen-install-indexer lumen-install-pipeline lumen-run \
        lumen-mcp lumen-security-audit lumen-test lumen-docker-build lumen-docker-rebuild \
        lumen-docker-run lumen-docker-mcp lumen-docker-security-audit lumen-docker-docs \
        lumen-native-build release


# -- Docker Mode --
DOCKER_IMAGE ?= lumen
LUMEN_VERSION := $(shell grep '^version = "' pipeline/pyproject.toml | sed 's/version = "\(.*\)"/\1/')

lumen-docker-build:
	docker build -t $(DOCKER_IMAGE) -t $(DOCKER_IMAGE):$(LUMEN_VERSION) .
	docker image prune -f

lumen-docker-rebuild:
	docker build --no-cache -t $(DOCKER_IMAGE) -t $(DOCKER_IMAGE):$(LUMEN_VERSION) .
	docker image prune -f

lumen-docker-run:
	@if [ -z "$(REPO)" ]; then echo "Usage: make lumen-docker-run REPO=/path/to/repo [ARGS='--provider ollama ... [--allow-xlarge]']"; exit 1; fi
	REPO="$(REPO)" OUTPUT_PATH="$(PWD)/output" DOCKER_IMAGE="$(DOCKER_IMAGE)" ARGS="$(ARGS)" \
	  ./scripts/lumen-docker-run.sh

lumen-docker-mcp:
	@if [ -z "$(DB)" ] && [ -z "$(REPO)" ]; then \
	  echo "Usage: make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "   or: make lumen-docker-mcp REPO=/path/to/repo"; \
	  echo "PORT defaults to 8765."; \
	  exit 1; fi
	DB="$(DB)" REPO="$(REPO)" OUTPUT_PATH="$(PWD)/output" DOCKER_IMAGE="$(DOCKER_IMAGE)" PORT="$${PORT:-8765}" \
	  ./scripts/lumen-docker-mcp.sh

lumen-docker-security-audit:
	@if [ -z "$(REPO)" ]; then echo "Usage: make lumen-docker-security-audit REPO=/path/to/repo [ARGS='--provider ollama ... [--allow-xlarge]']"; exit 1; fi
	REPO="$(REPO)" OUTPUT_PATH="$(PWD)/output" DOCKER_IMAGE="$(DOCKER_IMAGE)" ARGS="$(ARGS)" \
	  ./scripts/lumen-docker-security-audit.sh

lumen-docker-docs:
	OUTPUT_PATH="$(PWD)/output" DOCKER_IMAGE="$(DOCKER_IMAGE)" PORT="$${PORT:-8081}" ./scripts/lumen-docker-docs.sh

# -- Native distribution tarball --
lumen-native-build:
	bash scripts/build-native.sh $(if $(VERSION),--version $(VERSION))

# -- Release: bump version, commit, tag --
release:
	@if [ -z "$(VERSION)" ]; then echo "Usage: make release VERSION=0.2.0"; exit 1; fi
	bash scripts/bump-version.sh "$(VERSION)"
	git add pipeline/pyproject.toml indexer/parsers/javascript/package.json pipeline/uv.lock
	git commit -m "release: v$(VERSION)"
	git tag "v$(VERSION)"
	@echo ""
	@echo "Tagged v$(VERSION). Push to trigger CI release:"
	@echo "  git push origin main v$(VERSION)"

# -- Native install (requires Java 21, Node 18, Python 3.11) --
lumen-install: lumen-install-indexer lumen-install-pipeline

lumen-install-indexer:
	cd indexer && bash install.sh

lumen-install-pipeline:
	cd pipeline && uv sync

lumen-run:
	@if [ -z "$(REPO)" ]; then \
	  echo "Usage: make lumen-run REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
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

lumen-security-audit:
	@if [ -z "$(REPO)" ]; then \
	  echo "Usage: make lumen-security-audit REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
	  echo "  Anthropic: ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	  echo "  Ollama:    ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'"; \
	  echo "  OpenAI:    ARGS='--provider openai --model gpt-4o'"; \
	  exit 1; fi
	@if [ -z "$(ARGS)" ]; then \
	  echo "ERROR: ARGS is required. Pass --provider, --model, and optionally --base-url."; \
	  echo "  Anthropic: ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	  echo "  Ollama:    ARGS='--provider ollama --model qwen2.5:32b --base-url http://127.0.0.1:11434/v1'"; \
	  exit 1; fi
	cd pipeline && uv run lumen security-audit "$(REPO)" --output-dir "$(PWD)/output" $(ARGS)

lumen-mcp:
	@if [ -z "$(DB)" ] && [ -z "$(REPO)" ]; then \
	  echo "Usage: make lumen-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "   or: make lumen-mcp REPO=/path/to/repo"; \
	  echo "PORT defaults to 8765."; \
	  exit 1; fi
	@if [ -n "$(DB)" ] && [ -n "$(REPO)" ]; then \
	  cd pipeline && uv run lumen mcp-http --db-path "$(DB)" --repo-path "$(REPO)" --repo-name "$(notdir $(REPO))" --output-dir "$(PWD)/output" $(ARGS); \
	elif [ -n "$(DB)" ]; then \
	  cd pipeline && uv run lumen mcp-http --db-path "$(DB)" --output-dir "$(PWD)/output" $(ARGS); \
	else \
	  cd pipeline && uv run lumen mcp-http "$(REPO)" --repo-name "$(notdir $(REPO))" --output-dir "$(PWD)/output" $(ARGS); \
	fi

lumen-test:
	cd pipeline && python tests/_test_kuzu.py

lumen-help:
	@if [ -n "$(CMD)" ]; then \
	  case "$(CMD)" in \
	    lumen-docker-run) \
	      echo "make lumen-docker-run"; \
	      echo ""; \
	      echo "Run preflight + index + multi-agent analysis + docs build in Docker."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  ARGS='--provider <p> --model <m> [--base-url <url>] [--allow-xlarge] [other lumen run flags]'"; \
	      echo "  DOCKER_IMAGE=lumen"; \
	      echo ""; \
	      echo "Example:"; \
	      echo "  make lumen-docker-run REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	      echo "  make lumen-docker-run REPO=/path/to/repo ARGS='--allow-xlarge --provider anthropic --model claude-sonnet-4-6'"; \
	      ;; \
	    lumen-docker-mcp) \
	      echo "make lumen-docker-mcp"; \
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
	      echo "  make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	      echo "  make lumen-docker-mcp REPO=/path/to/repo PORT=9000"; \
	      ;; \
	    lumen-docker-security-audit) \
	      echo "make lumen-docker-security-audit"; \
	      echo ""; \
	      echo "Run preflight + index + security-audit agent stage (fan-out reviewers + risk synthesis) in Docker."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  ARGS='--provider <p> --model <m> [--base-url <url>] [--allow-xlarge] [other lumen security-audit flags]'"; \
	      echo "  DOCKER_IMAGE=lumen"; \
	      echo ""; \
	      echo "Example:"; \
	      echo "  make lumen-docker-security-audit REPO=/path/to/repo ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	      ;; \
	    lumen-docker-docs) \
	      echo "make lumen-docker-docs"; \
	      echo ""; \
	      echo "Rebuild and serve docs from ./output using the lumen Docker image."; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  PORT=8081"; \
	      echo "  DOCKER_IMAGE=lumen"; \
	      ;; \
	    lumen-run) \
	      echo "make lumen-run"; \
	      echo ""; \
	      echo "Run the full Lumen pipeline natively."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo "  ARGS='--provider <p> --model <m> [--base-url <url>] [other lumen run flags]'"; \
	      ;; \
	    lumen-security-audit) \
	      echo "make lumen-security-audit"; \
	      echo ""; \
	      echo "Run the security-audit Lumen pipeline natively."; \
	      echo ""; \
	      echo "Required:"; \
	      echo "  REPO=/path/to/repo"; \
	      echo "  ARGS='--provider <p> --model <m> [--base-url <url>] [other lumen security-audit flags]'"; \
	      ;; \
	    lumen-mcp) \
	      echo "make lumen-mcp"; \
	      echo ""; \
	      echo "Run HTTP MCP natively."; \
	      echo ""; \
	      echo "Provide at least one of:"; \
	      echo "  DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	      echo "  REPO=/path/to/repo"; \
	      echo ""; \
	      echo "Behavior:"; \
	      echo "  DB=...           serve an existing indexed DB"; \
	      echo "  REPO=...         run preflight + index + MCP"; \
	      echo "  DB=... REPO=...  serve an existing DB with repo-path context"; \
	      echo ""; \
	      echo "Optional:"; \
	      echo "  ARGS='--port 8765 --repo-size-check warn'"; \
	      ;; \
	    *) \
	      echo "Unknown CMD: $(CMD)"; \
	      echo "Use: make lumen-help"; \
	      exit 1; \
	      ;; \
	  esac; \
	else \
	  echo "Lumen Make Targets"; \
	  echo ""; \
	  echo "Docker:"; \
	  echo "  make lumen-docker-build"; \
	  echo "    Build the reusable '$(DOCKER_IMAGE)' image."; \
	  echo "  make lumen-docker-run REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>] [--allow-xlarge]'"; \
	  echo "    Run preflight + index + multi-agent analysis + docs build in Docker."; \
	  echo "  make lumen-docker-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "    Serve an existing indexed DB over HTTP MCP in Docker."; \
	  echo "  make lumen-docker-mcp REPO=/path/to/repo"; \
	  echo "    Run preflight + index + HTTP MCP in Docker."; \
	  echo "  make lumen-docker-security-audit REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
	  echo "    Run preflight + index + security-audit agent stage in Docker."; \
	  echo "  make lumen-docker-docs"; \
	  echo "    Rebuild and serve the generated MkDocs site from ./output in Docker."; \
	  echo ""; \
	  echo "Native:"; \
	  echo "  make lumen-install"; \
	  echo "    Build indexers and sync the Python pipeline environment."; \
	  echo "  make lumen-native-build [VERSION=x.y.z]"; \
	  echo "    Build a self-contained platform tarball in releases/."; \
	  echo "  make lumen-run REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
	  echo "    Run the full Lumen pipeline natively."; \
	  echo "  make lumen-security-audit REPO=/path/to/repo ARGS='--provider <p> --model <m> [--base-url <url>]'"; \
	  echo "    Run the security-audit Lumen pipeline natively."; \
	  echo "  make lumen-mcp DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "    Run HTTP MCP natively against an existing DB."; \
	  echo "  make lumen-mcp REPO=/path/to/repo"; \
	  echo "    Run preflight + index + HTTP MCP natively."; \
	  echo ""; \
	  echo "Common variables:"; \
	  echo "  REPO=/path/to/repo"; \
	  echo "  DB=/path/to/output/<run>/index.kuzu/<repo>-db"; \
	  echo "  ARGS='--provider anthropic --model claude-sonnet-4-6'"; \
	  echo "  PORT=8765"; \
	  echo "  DOCKER_IMAGE=lumen"; \
	  echo ""; \
	  echo "Per-command help:"; \
	  echo "  make lumen-help CMD=lumen-docker-mcp"; \
	  echo "  make lumen-help CMD=lumen-mcp"; \
	  echo "  make lumen-help CMD=lumen-docker-security-audit"; \
	  echo "  make lumen-help CMD=lumen-security-audit"; \
	fi
