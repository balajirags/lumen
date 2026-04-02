.PHONY: install install-indexer install-pipeline dev-ui test \
        docker-build docker-run \
        compose-pipeline compose-docs compose-ui

# ── Native install (requires Java 21, Node 18, Python 3.11) ──
install: install-indexer install-pipeline

install-indexer:
	cd indexer && bash install.sh

install-pipeline:
	cd pipeline && uv pip install -e .

dev-ui:
	cd ui && npm install && npm run dev

test:
	cd pipeline && python tests/_test_kuzu.py

# ── Docker ──
DOCKER_IMAGE ?= lumen

docker-build:
	docker build -t $(DOCKER_IMAGE) .

docker-run:
	@if [ -z "$(REPO)" ]; then echo "Usage: make docker-run REPO=/path/to/repo"; exit 1; fi
	docker run --rm \
	  -v "$(REPO)":/repo \
	  -v "$(PWD)/output":/workspace/output \
	  -e ANTHROPIC_API_KEY="$(ANTHROPIC_API_KEY)" \
	  $(DOCKER_IMAGE) run /repo --output-dir /workspace/output

# ── Docker Compose ──
compose-pipeline:
	@if [ -z "$(REPO)" ]; then echo "Usage: make compose-pipeline REPO=/path/to/repo [ARGS='--provider ollama ...']"; exit 1; fi
	REPO_PATH="$(REPO)" OUTPUT_PATH="$(PWD)/output" COMPOSE_PROFILES=pipeline \
	  docker-compose run pipeline -- \
	  run /repo --output-dir /workspace/output $(ARGS)

compose-docs:
	OUTPUT_PATH="$(PWD)/output" COMPOSE_PROFILES=docs docker-compose up docs

compose-ui:
	OUTPUT_PATH="$(PWD)/output" COMPOSE_PROFILES=ui docker-compose up ui
