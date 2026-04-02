.PHONY: install install-indexer install-pipeline dev-ui test

install: install-indexer install-pipeline

install-indexer:
	cd indexer && bash install.sh

install-pipeline:
	cd pipeline && pip install -e .

dev-ui:
	cd ui && npm install && npm run dev

test:
	cd pipeline && python tests/_test_kuzu.py
