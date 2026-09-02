#!/usr/bin/env bash

set -euo pipefail

DOCKER_IMAGE="${DOCKER_IMAGE:-lumen}"
PORT="${PORT:-8081}"
OUTPUT_PATH="${OUTPUT_PATH:-$(pwd)/output}"

mkdir -p "${OUTPUT_PATH}"

docker_args=(
  run --rm
  -p "${PORT}:8080"
  -v "${OUTPUT_PATH}:/workspace/output"
  -e OUTPUT_DIR=/workspace/output
  -e SITE_DIR=/workspace/output/doc-site
  -e SITE_TITLE="lumen Docs"
  -e DOCS_PORT=8080
  -e HOST_PORT="${PORT}"
  --entrypoint /opt/lumen/scripts/run-docs-server.sh
)

if [ -t 0 ]; then
  docker_args+=( -i )
fi
if [ -t 1 ]; then
  docker_args+=( -t )
fi

docker "${docker_args[@]}" "${DOCKER_IMAGE}"
