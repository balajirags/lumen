#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-/workspace/output}"
SITE_DIR="${SITE_DIR:-$OUTPUT_DIR/doc-site}"
SITE_TITLE="${SITE_TITLE:-lumen Docs}"
DOCS_PORT="${DOCS_PORT:-8080}"

bash /opt/lumen/scripts/build-docs-site.sh \
  --output-dir "$OUTPUT_DIR" \
  --site-dir "$SITE_DIR" \
  --title "$SITE_TITLE"

exec python -m http.server "$DOCS_PORT" --directory "$SITE_DIR"
