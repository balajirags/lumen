#!/usr/bin/env bash

set -euo pipefail

DOCKER_IMAGE="${DOCKER_IMAGE:-lumen}"
IMAGE_TAR="${1:-${IMAGE_TAR:-}}"

if [ -z "${IMAGE_TAR}" ]; then
  echo "Usage: ./scripts/lumen-docker-load.sh /path/to/lumen-image.tar"
  echo "   or: IMAGE_TAR=/path/to/lumen-image.tar ./scripts/lumen-docker-load.sh"
  exit 1
fi

# Load and capture the imported tag (e.g. "Loaded image: lumen:1.1.3")
LOAD_OUTPUT="$(docker load -i "${IMAGE_TAR}")"
echo "${LOAD_OUTPUT}"

LOADED_TAG="$(echo "${LOAD_OUTPUT}" | grep -oE '[^ ]+:[^ ]+$' | head -1)"

# Ensure lumen:latest exists so all scripts work
if [ -n "${LOADED_TAG}" ] && [ "${LOADED_TAG}" != "${DOCKER_IMAGE}:latest" ]; then
  docker tag "${LOADED_TAG}" "${DOCKER_IMAGE}:latest"
  echo "Tagged ${LOADED_TAG} → ${DOCKER_IMAGE}:latest"
fi
