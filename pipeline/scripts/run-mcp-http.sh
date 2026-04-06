#!/usr/bin/env sh
set -eu

HOST="${MCP_HOST:-0.0.0.0}"
PORT="${MCP_PORT:-8765}"
MCP_PATH="${MCP_PATH:-/mcp}"

if [ -n "${DB_PATH:-}" ]; then
  set -- lumen mcp-http --db-path "${DB_PATH}" --host "${HOST}" --port "${PORT}" --path "${MCP_PATH}"
  if [ -n "${REPO_PATH:-}" ]; then
    set -- "$@" --repo-path "${REPO_PATH}"
  fi
  exec "$@"
fi

if [ -z "${REPO_PATH:-}" ]; then
  echo "DB_PATH or REPO_PATH is required" >&2
  exit 1
fi

REPO_NAME="${REPO_NAME:-$(basename "${REPO_PATH}")}"

exec lumen mcp-http "${REPO_PATH}" \
  --repo-name "${REPO_NAME}" \
  --output-dir /workspace/output \
  --host "${HOST}" \
  --port "${PORT}" \
  --path "${MCP_PATH}"
