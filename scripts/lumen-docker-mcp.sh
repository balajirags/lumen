#!/usr/bin/env bash

set -euo pipefail

DOCKER_IMAGE="${DOCKER_IMAGE:-lumen}"
REPO="${REPO:-}"
DB="${DB:-}"
PORT="${PORT:-8765}"
OUTPUT_PATH="${OUTPUT_PATH:-$(pwd)/output}"

if [ -z "${DB}" ] && [ -z "${REPO}" ]; then
  echo "Usage: DB=/path/to/output/<run>/index.kuzu/<repo>-db ./scripts/lumen-docker-mcp.sh"
  echo "   or: REPO=/path/to/repo ./scripts/lumen-docker-mcp.sh"
  exit 1
fi

mkdir -p "${OUTPUT_PATH}"

REPO_PATH_ENV=""
REPO_NAME_ENV=""
if [ -n "${REPO}" ]; then
  REPO_PATH_ENV="/repo"
  REPO_NAME_ENV="$(basename "${REPO}")"
fi

docker_args=(
  run --rm
  --add-host=host.docker.internal:host-gateway
  -p "${PORT}:${PORT}"
  -v "${OUTPUT_PATH}:/workspace/output"
)

if [ -n "${REPO}" ]; then
  docker_args+=( -v "${REPO}:/repo" )
fi
if [ -n "${DB}" ]; then
  docker_args+=( -v "${DB}:${DB}" )
fi
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  docker_args+=( -e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" )
fi
if [ -n "${OPENAI_API_KEY:-}" ]; then
  docker_args+=( -e "OPENAI_API_KEY=${OPENAI_API_KEY}" )
fi

docker_args+=(
  -e "REPO_PATH=${REPO_PATH_ENV}"
  -e "REPO_NAME=${REPO_NAME_ENV}"
  -e "DB_PATH=${DB}"
  -e "MCP_PORT=${PORT}"
  -e "MCP_HOST=0.0.0.0"
  -e "MCP_PATH=/mcp"
  --entrypoint /opt/lumen/scripts/run-mcp-http.sh
)

docker "${docker_args[@]}" "${DOCKER_IMAGE}"
