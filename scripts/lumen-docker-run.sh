#!/usr/bin/env bash

set -euo pipefail

DOCKER_IMAGE="${DOCKER_IMAGE:-lumen}"
REPO="${REPO:-}"
ARGS="${ARGS:-}"
OUTPUT_PATH="${OUTPUT_PATH:-$(pwd)/output}"

if [ -z "${REPO}" ]; then
  echo "Usage: REPO=/path/to/repo [ARGS='--provider anthropic --model claude-sonnet-4-6'] ./scripts/lumen-docker-run.sh"
  exit 1
fi

mkdir -p "${OUTPUT_PATH}"

docker_args=(
  run --rm
  --add-host=host.docker.internal:host-gateway
  -v "${REPO}:/repo"
  -v "${OUTPUT_PATH}:/workspace/output"
)

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  docker_args+=( -e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" )
fi
if [ -n "${OPENAI_API_KEY:-}" ]; then
  docker_args+=( -e "OPENAI_API_KEY=${OPENAI_API_KEY}" )
fi

cmd=( "${DOCKER_IMAGE}" run /repo --repo-name "$(basename "${REPO}")" --output-dir /workspace/output )
if [ -n "${ARGS}" ]; then
  # shellcheck disable=SC2206
  extra_args=( ${ARGS} )
  cmd+=( "${extra_args[@]}" )
fi

docker "${docker_args[@]}" "${cmd[@]}"
