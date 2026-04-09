#!/usr/bin/env bash

set -euo pipefail

IMAGE_TAR="${1:-${IMAGE_TAR:-}}"

if [ -z "${IMAGE_TAR}" ]; then
  echo "Usage: ./scripts/lumen-docker-load.sh /path/to/lumen-image.tar"
  echo "   or: IMAGE_TAR=/path/to/lumen-image.tar ./scripts/lumen-docker-load.sh"
  exit 1
fi

docker load -i "${IMAGE_TAR}"
