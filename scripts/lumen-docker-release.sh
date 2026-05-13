#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_IMAGE="${DOCKER_IMAGE:-lumen}"
RELEASE_DIR="${RELEASE_DIR:-${ROOT_DIR}/releases}"
TAG="${TAG:-}"
_PY_VERSION="$(grep '^version = "' "${ROOT_DIR}/pipeline/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')"
VERSION="${VERSION:-${TAG:-${_PY_VERSION}}}"
DOCKER_TAG="${DOCKER_TAG:-${VERSION}}"
IMAGE_REF="${DOCKER_IMAGE}:${DOCKER_TAG}"
ARCH="${ARCH:-$(uname -m)}"
case "${ARCH}" in
  arm64|aarch64) DOCKER_PLATFORM="linux/arm64" ;;
  x86_64|amd64)  DOCKER_PLATFORM="linux/amd64" ;;
  *)             DOCKER_PLATFORM="linux/${ARCH}" ;;
esac
RC_NAME="${RC_NAME:-${DOCKER_IMAGE}-docker-${VERSION}-${ARCH}}"
RC_DIR="${RELEASE_DIR}/${RC_NAME}"
IMAGES_DIR="${RC_DIR}/images"
RUNTIME_DIR="${RC_DIR}/runtime"
SCRIPTS_DIR="${RUNTIME_DIR}/scripts"
CHECKSUMS_DIR="${RC_DIR}/checksums"
IMAGE_TAR="${IMAGES_DIR}/${DOCKER_IMAGE}-${VERSION}-${ARCH}.tar"
BUNDLE_TGZ="${RELEASE_DIR}/${RC_NAME}.tar.gz"

echo "Preparing release candidate: ${RC_NAME}"
echo "Image: ${IMAGE_REF}"

if ! docker image inspect "${IMAGE_REF}" >/dev/null 2>&1; then
  echo "Local image ${IMAGE_REF} not found. Running make lumen-docker-build..."
  (
    cd "${ROOT_DIR}"
    make lumen-docker-build DOCKER_IMAGE="${DOCKER_IMAGE}"
  )
else
  echo "Found existing local image ${IMAGE_REF}"
fi

mkdir -p "${IMAGES_DIR}" "${RUNTIME_DIR}" "${SCRIPTS_DIR}" "${CHECKSUMS_DIR}"

echo "Saving image archive to ${IMAGE_TAR}"
# docker save omits layer blobs when Docker uses a containerd-backed storage driver (Colima,
# some Docker Desktop configs), producing a ~12 KB stub instead of the full image.
# docker buildx build with type=docker output always includes all layers.
# Prefer host buildx; fall back to buildx inside the Colima VM if available.
if docker buildx version >/dev/null 2>&1; then
  (
    cd "${ROOT_DIR}"
    docker buildx build \
      --platform "${DOCKER_PLATFORM}" \
      --output "type=docker,name=${IMAGE_REF},dest=${IMAGE_TAR}" \
      .
  )
elif command -v colima >/dev/null 2>&1 && colima ssh -- docker buildx version >/dev/null 2>&1; then
  echo "Host docker buildx not found; using buildx inside Colima VM"
  colima ssh -- docker buildx build \
    --platform "${DOCKER_PLATFORM}" \
    --output "type=docker,name=${IMAGE_REF},dest=${IMAGE_TAR}" \
    "${ROOT_DIR}"
else
  echo "ERROR: docker buildx is required for reliable image export with containerd storage."
  echo "Install on Mac:"
  echo "  brew install docker-buildx"
  echo "  mkdir -p ~/.docker/cli-plugins"
  # shellcheck disable=SC2016
  echo '  ln -sfn $(brew --prefix)/opt/docker-buildx/bin/docker-buildx ~/.docker/cli-plugins/docker-buildx'
  exit 1
fi

echo "Copying runtime files"
cp "${ROOT_DIR}/Makefile" "${RUNTIME_DIR}/Makefile"
cp "${ROOT_DIR}/run-lumen.md" "${RUNTIME_DIR}/run-lumen.md"
cp "${ROOT_DIR}/docker-source.md" "${RUNTIME_DIR}/docker-source.md"
cp "${ROOT_DIR}/scripts/lumen-docker-load.sh" "${SCRIPTS_DIR}/lumen-docker-load.sh"
cp "${ROOT_DIR}/scripts/lumen-docker-run.sh" "${SCRIPTS_DIR}/lumen-docker-run.sh"
cp "${ROOT_DIR}/scripts/lumen-docker-mcp.sh" "${SCRIPTS_DIR}/lumen-docker-mcp.sh"
cp "${ROOT_DIR}/scripts/lumen-docker-docs.sh" "${SCRIPTS_DIR}/lumen-docker-docs.sh"
chmod +x "${SCRIPTS_DIR}/lumen-docker-load.sh" "${SCRIPTS_DIR}/lumen-docker-run.sh" "${SCRIPTS_DIR}/lumen-docker-mcp.sh" "${SCRIPTS_DIR}/lumen-docker-docs.sh"

cat > "${RUNTIME_DIR}/release.txt" <<EOF
release_candidate=${RC_NAME}
image_ref=${IMAGE_REF}
version=${VERSION}
arch=${ARCH}
created_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

echo "Generating checksums"
(
  cd "${RC_DIR}"
  shasum -a 256 \
    "images/$(basename "${IMAGE_TAR}")" \
    "runtime/Makefile" \
    "runtime/run-lumen.md" \
    "runtime/docker-source.md" \
    "runtime/release.txt" \
    "runtime/scripts/lumen-docker-load.sh" \
    "runtime/scripts/lumen-docker-run.sh" \
    "runtime/scripts/lumen-docker-mcp.sh" \
    "runtime/scripts/lumen-docker-docs.sh" \
    > "${CHECKSUMS_DIR}/SHA256SUMS"
)

echo "Creating bundle archive ${BUNDLE_TGZ}"
tar -C "${RELEASE_DIR}" -czf "${BUNDLE_TGZ}" "${RC_NAME}"

echo
echo "Release candidate created:"
echo "  Bundle: ${BUNDLE_TGZ}"
echo "  Folder: ${RC_DIR}"
echo "  Image tar: ${IMAGE_TAR}"
