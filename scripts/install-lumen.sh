#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-lumen.sh — Download and install the lumen native bundle
#
# One-line install:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/lumen/main/scripts/install-lumen.sh | bash
#
# Review before running:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/lumen/main/scripts/install-lumen.sh -o install-lumen.sh
#   less install-lumen.sh
#   bash install-lumen.sh
#
# Pin a specific version:
#   VERSION=v0.1.0 bash install-lumen.sh
#
# Custom install location:
#   LUMEN_INSTALL_DIR=~/tools/lumen bash install-lumen.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GITHUB_REPO="<owner>/lumen"
INSTALL_DIR="${LUMEN_INSTALL_DIR:-$HOME/.local/share/lumen}"
BIN_DIR="${LUMEN_BIN_DIR:-$HOME/.local/bin}"

# ── Portable SHA256 helper ────────────────────────────────────────────────────
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# ── Platform detection ────────────────────────────────────────────────────────
detect_platform() {
  local os arch
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$os" in
    linux)  os="linux" ;;
    darwin) os="darwin" ;;
    *)
      echo "ERROR: Unsupported OS: $os"
      echo "  Supported: linux, darwin (macOS)"
      exit 1
      ;;
  esac

  arch="$(uname -m)"
  case "$arch" in
    x86_64)        arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *)
      echo "ERROR: Unsupported architecture: $arch"
      echo "  Supported: x86_64/amd64, aarch64/arm64"
      exit 1
      ;;
  esac

  PLATFORM="${os}-${arch}"
}

# ── Resolve version ──────────────────────────────────────────────────────────
resolve_version() {
  if [ -n "${VERSION:-}" ]; then
    # User pinned a version
    RESOLVED_VERSION="$VERSION"
    return
  fi

  echo "==> Resolving latest release..."
  local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
  local response
  if command -v curl >/dev/null 2>&1; then
    response="$(curl -fsSL "$api_url" 2>/dev/null)" || {
      echo "ERROR: Failed to fetch latest release from GitHub API."
      echo "  URL: $api_url"
      echo "  Tip: Set VERSION=vX.Y.Z to skip API lookup."
      exit 1
    }
  elif command -v wget >/dev/null 2>&1; then
    response="$(wget -qO- "$api_url" 2>/dev/null)" || {
      echo "ERROR: Failed to fetch latest release from GitHub API."
      exit 1
    }
  else
    echo "ERROR: curl or wget is required."
    exit 1
  fi

  # Extract tag_name without jq (portable)
  RESOLVED_VERSION="$(printf '%s' "$response" | grep '"tag_name"' | head -1 \
    | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')"

  if [ -z "$RESOLVED_VERSION" ]; then
    echo "ERROR: Could not determine latest release version."
    echo "  Tip: Set VERSION=vX.Y.Z to specify a version explicitly."
    exit 1
  fi
}

# ── Download and verify ──────────────────────────────────────────────────────
download_and_verify() {
  local version_bare="${RESOLVED_VERSION#v}"  # strip leading 'v'
  BUNDLE_NAME="lumen-${version_bare}-${PLATFORM}"
  local tarball_name="${BUNDLE_NAME}.tar.gz"
  local base_url="https://github.com/${GITHUB_REPO}/releases/download/${RESOLVED_VERSION}"
  local tarball_url="${base_url}/${tarball_name}"
  local sha256_url="${base_url}/${tarball_name}.sha256"

  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT

  local tarball_path="${tmpdir}/${tarball_name}"
  local sha256_path="${tmpdir}/${tarball_name}.sha256"

  echo "==> Downloading lumen ${RESOLVED_VERSION} for ${PLATFORM}..."
  echo "    Tarball: $tarball_url"

  if command -v curl >/dev/null 2>&1; then
    curl -fSL --progress-bar -o "$tarball_path" "$tarball_url" || {
      echo "ERROR: Failed to download tarball."
      echo "  URL: $tarball_url"
      echo "  Check that version ${RESOLVED_VERSION} has a release for ${PLATFORM}."
      exit 1
    }
    curl -fsSL -o "$sha256_path" "$sha256_url" || {
      echo "ERROR: Failed to download SHA256 checksum."
      echo "  URL: $sha256_url"
      exit 1
    }
  elif command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "$tarball_path" "$tarball_url" || {
      echo "ERROR: Failed to download tarball."
      exit 1
    }
    wget -qO "$sha256_path" "$sha256_url" || {
      echo "ERROR: Failed to download SHA256 checksum."
      exit 1
    }
  fi

  # ── SHA256 verification ──────────────────────────────────────────────────
  echo "==> Verifying SHA256 checksum..."
  local expected actual
  expected="$(awk '{print $1}' "$sha256_path")"
  actual="$(sha256_file "$tarball_path")"

  if [ "$actual" != "$expected" ]; then
    echo "ERROR: SHA256 checksum mismatch!"
    echo "  expected: $expected"
    echo "  actual:   $actual"
    echo ""
    echo "The downloaded file may have been tampered with."
    echo "Aborting installation."
    exit 1
  fi
  echo "    Checksum OK: $actual"

  # ── Extract ──────────────────────────────────────────────────────────────
  echo "==> Installing to ${INSTALL_DIR}..."
  rm -rf "$INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  tar -xzf "$tarball_path" -C "$(dirname "$INSTALL_DIR")"
  # The tarball extracts as lumen-VERSION-os-arch/; rename to install dir
  mv "$(dirname "$INSTALL_DIR")/${BUNDLE_NAME}" "$INSTALL_DIR"
}

# ── Symlink into PATH ────────────────────────────────────────────────────────
install_link() {
  mkdir -p "$BIN_DIR"
  ln -sf "$INSTALL_DIR/lumen" "$BIN_DIR/lumen"
  echo "    Linked: $BIN_DIR/lumen -> $INSTALL_DIR/lumen"
}

# ── Post-install checks ─────────────────────────────────────────────────────
post_install_checks() {
  # Check PATH
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      echo ""
      echo "NOTE: $BIN_DIR is not in your PATH. Add it:"
      echo "  export PATH=\"$BIN_DIR:\$PATH\""
      echo ""
      echo "To make this permanent, add the line above to your ~/.bashrc or ~/.zshrc"
      ;;
  esac

  # Check graphviz
  if ! command -v dot >/dev/null 2>&1; then
    echo ""
    echo "NOTE: graphviz is required for docs generation but was not found."
    case "$(uname -s)" in
      Darwin) echo "  Install: brew install graphviz" ;;
      Linux)  echo "  Install: sudo apt install graphviz  (or: sudo yum install graphviz)" ;;
    esac
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo "╔═══════════════════════════════════════════════╗"
  echo "║          lumen — native installer             ║"
  echo "╚═══════════════════════════════════════════════╝"
  echo ""

  detect_platform
  resolve_version

  echo ""
  echo "  Version:    ${RESOLVED_VERSION}"
  echo "  Platform:   ${PLATFORM}"
  echo "  Install to: ${INSTALL_DIR}"
  echo "  Bin link:   ${BIN_DIR}/lumen"
  echo ""

  download_and_verify
  install_link

  echo ""
  echo "═══════════════════════════════════════════════════"
  echo "  lumen ${RESOLVED_VERSION} installed successfully!"
  echo ""
  echo "  Run:  lumen run /path/to/repo \\"
  echo "          --provider anthropic --model claude-sonnet-4-6"
  echo "═══════════════════════════════════════════════════"

  post_install_checks
  echo ""
}

main
