.PHONY: install install-indexer install-pipeline dev-ui test docker-build docker-run

# ── Native install (requires Java 21, Node 18, Python 3.11) ──
install: install-indexer install-pipeline

install-indexer:
	cd indexer && bash install.sh

install-pipeline:
	cd pipeline && pip install -e .

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
