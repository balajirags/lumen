#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build-native.sh — Build a self-contained lumen platform tarball
#
# Bundles all runtimes so end users need only extract and run — no Docker,
# no Java, no Node, no Python required on the target machine.
#
# Bundle layout:
#   lumen-{VERSION}-{os}-{arch}/
#     lumen              ← relocatable shell launcher (add to PATH)
#     install.sh         ← symlinks lumen into ~/.local/bin
#     bin/               ← cmg-java, cmg-js, cmg-python, cmg-php, plantuml (relocatable)
#     jre/               ← jlink minimal JRE
#     lib/               ← code-mem-graph.jar, plantuml.jar
#     node/bin/node      ← Node binary (for cmg-js parser)
#     indexer/parsers/   ← JS parser + node_modules, Python parser, PHP parser + vendor
#     venv/              ← Python venv with lumen + all deps
#     scripts/           ← build-docs-site.sh and other pipeline scripts
#
# Prerequisites (build machine only — NOT required on target):
#   - Java 21 JDK  (gradle shadowJar + jdeps + jlink)
#   - Node 20      (npm ci for JS parser)
#   - Python 3.11+ (venv + pip install lumen)
#   - curl         (PlantUML download)
#   - PHP + Composer (optional — for PHP repos; vendor/ copied from existing install or
#                     re-installed via Composer; skipped with a warning if neither present)
#
# Runtime prerequisites on target (cannot be bundled):
#   - graphviz (for pygraphviz / PlantUML rendering in docs build)
#     macOS: brew install graphviz
#     Linux: apt install graphviz  / yum install graphviz
#   - php (only for indexing PHP repos — not needed for Java/JS/Python repos)
#     macOS: brew install php
#     Linux: apt install php-cli  / yum install php-cli
#
# Usage:
#   bash scripts/build-native.sh
#   make native-build
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Version: read from pyproject.toml (single source of truth) ───────────────
# Override with: bash build-native.sh --version v1.2.3
PYPROJECT_VERSION="$(grep -m1 '^version' "$REPO_ROOT/pipeline/pyproject.toml" \
                     | sed 's/.*= *"\(.*\)"/\1/')"
VERSION="v${PYPROJECT_VERSION}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2}"; shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# ── PlantUML pinned release (keep in sync with Dockerfile) ───────────────────
PLANTUML_VERSION="1.2026.2"
PLANTUML_SHA256="3cdce52133c424dea22425b947ae9d47f2167b0866dfcf99e714d4ea1689975c"

# ── Platform detection ────────────────────────────────────────────────────────
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  x86_64)        ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "ERROR: Unsupported architecture: $ARCH_RAW"; exit 1 ;;
esac

BUNDLE_NAME="lumen-${VERSION}-${OS}-${ARCH}"
DIST_DIR="$REPO_ROOT/dist/$BUNDLE_NAME"
RELEASES_DIR="$REPO_ROOT/releases"

# ── Portable SHA256 helper ────────────────────────────────────────────────────
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

verify_sha256() {
  local file="$1" expected="$2"
  local actual
  actual="$(sha256_file "$file")"
  if [ "$actual" != "$expected" ]; then
    echo "ERROR: SHA256 mismatch for $(basename "$file")"
    echo "  expected: $expected"
    echo "  actual:   $actual"
    exit 1
  fi
}

# ── Prerequisite checks ───────────────────────────────────────────────────────
echo "==> Checking prerequisites..."
missing=0
for cmd_label in "java:Java 21 JDK" "node:Node 20" "python3:Python 3.11+" "uv:uv (astral.sh/uv)" "curl:curl"; do
  cmd="${cmd_label%%:*}"
  label="${cmd_label#*:}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "  MISSING: $label ($cmd)"
    missing=1
  fi
done
[ "$missing" -eq 1 ] && exit 1

echo "  java:   $(java -version 2>&1 | head -1)"
echo "  node:   $(node --version)"
echo "  python: $(python3 --version)"
echo ""
echo "==> Building lumen ${VERSION} for ${OS}-${ARCH}"
echo "    Output: $DIST_DIR"
echo ""

# ── Clean and create dist dir ─────────────────────────────────────────────────
rm -rf "$DIST_DIR"
mkdir -p \
  "$DIST_DIR/bin" \
  "$DIST_DIR/lib" \
  "$DIST_DIR/node/bin" \
  "$DIST_DIR/indexer/parsers" \
  "$DIST_DIR/scripts"
# NOTE: $DIST_DIR/jre is NOT pre-created — jlink requires --output to be a new directory

# ── Step 1: Java fat JAR ──────────────────────────────────────────────────────
echo "--- Step 1: Building Java fat JAR ---"
(cd "$REPO_ROOT/indexer" && ./gradlew shadowJar --no-daemon -q)
JAR_SRC="$REPO_ROOT/indexer/app/build/libs/code-mem-graph.jar"
[ -f "$JAR_SRC" ] || { echo "ERROR: JAR not found at $JAR_SRC"; exit 1; }
cp "$JAR_SRC" "$DIST_DIR/lib/code-mem-graph.jar"
echo "  ✓ lib/code-mem-graph.jar"

# ── Step 2: jlink minimal JRE ─────────────────────────────────────────────────
echo "--- Step 2: Building minimal JRE via jlink ---"
JDEPS_OUT="$(jdeps --ignore-missing-deps \
                   --print-module-deps \
                   --multi-release 21 \
                   "$DIST_DIR/lib/code-mem-graph.jar" 2>/dev/null || true)"
BASELINE="java.base,java.sql,java.logging,java.naming,java.management,java.net.http,java.security.jgss,java.security.sasl,java.xml,jdk.unsupported,jdk.crypto.ec"
MODULES="$(printf '%s\n' "$JDEPS_OUT" "$BASELINE" \
           | tr ',' '\n' | grep -v '^$' | sort -u \
           | tr '\n' ',' | sed 's/,$//')"
jlink \
  --add-modules "$MODULES" \
  --strip-debug \
  --no-man-pages \
  --no-header-files \
  --output "$DIST_DIR/jre"
echo "  ✓ jre/ ($(du -sh "$DIST_DIR/jre" | cut -f1))"

# ── Step 3: PlantUML JAR ──────────────────────────────────────────────────────
echo "--- Step 3: Downloading PlantUML ${PLANTUML_VERSION} ---"
PLANTUML_URL="https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar"
curl -fsSL -o "$DIST_DIR/lib/plantuml.jar" "$PLANTUML_URL"
verify_sha256 "$DIST_DIR/lib/plantuml.jar" "$PLANTUML_SHA256"
echo "  ✓ lib/plantuml.jar"

# ── Step 4: Node binary ───────────────────────────────────────────────────────
echo "--- Step 4: Copying Node binary ---"
NODE_BIN="$(command -v node)"
# Resolve symlinks so we copy the real binary, not a dangling link
if command -v realpath >/dev/null 2>&1; then
  NODE_BIN="$(realpath "$NODE_BIN")"
elif [ -L "$NODE_BIN" ]; then
  NODE_BIN="$(readlink -f "$NODE_BIN" 2>/dev/null || readlink "$NODE_BIN")"
fi
cp "$NODE_BIN" "$DIST_DIR/node/bin/node"
chmod +x "$DIST_DIR/node/bin/node"
echo "  ✓ node/bin/node ($(node --version))"

# ── Step 5: JS parser ─────────────────────────────────────────────────────────
echo "--- Step 5: Installing JS parser ---"
(cd "$REPO_ROOT/indexer/parsers/javascript" && npm ci --omit=dev --silent)
# Prune build/dev artefacts from node_modules (mirrors Dockerfile node-builder stage)
(cd "$REPO_ROOT/indexer/parsers/javascript" && \
  rm -rf node_modules/kuzu/kuzu-source \
         node_modules/kuzu/prebuilt \
         node_modules/cmake-js \
         node_modules/node-addon-api \
         node_modules/node-api-headers 2>/dev/null || true && \
  find node_modules -type d \( -name test -o -name tests -o -name __tests__ \
       -o -name example -o -name examples -o -name docs -o -name doc \) \
       -exec rm -rf {} + 2>/dev/null || true && \
  find node_modules -type f \( -name "*.md" -o -name "*.ts" -o -name "CHANGELOG*" \
       -o -name "*.map" \) -delete 2>/dev/null || true && \
  rm -rf node_modules/.bin 2>/dev/null || true)
cp -r "$REPO_ROOT/indexer/parsers/javascript" "$DIST_DIR/indexer/parsers/javascript"
echo "  ✓ indexer/parsers/javascript/"

# ── Step 6: Python parser ─────────────────────────────────────────────────────
echo "--- Step 6: Copying Python parser ---"
cp -r "$REPO_ROOT/indexer/parsers/python" "$DIST_DIR/indexer/parsers/python"
echo "  ✓ indexer/parsers/python/"

# ── Step 7: PHP parser ────────────────────────────────────────────────────────
echo "--- Step 7: Copying PHP parser ---"
PHP_SRC="$REPO_ROOT/indexer/parsers/php"
PHP_INCLUDED=0
if [ ! -d "$PHP_SRC/vendor" ]; then
  if command -v composer >/dev/null 2>&1; then
    echo "  vendor/ not found — running composer install..."
    (cd "$PHP_SRC" && composer install --no-dev --quiet)
  else
    echo "  ⚠ Composer not found and vendor/ missing — PHP parser NOT included in bundle"
    echo "    Install Composer (https://getcomposer.org) and re-run to include PHP support"
    PHP_SRC=""
  fi
fi
if [ -n "$PHP_SRC" ] && [ -d "$PHP_SRC/vendor" ]; then
  cp -r "$PHP_SRC" "$DIST_DIR/indexer/parsers/php"
  PHP_INCLUDED=1
  echo "  ✓ indexer/parsers/php/"
fi

# ── Step 8: Python venv + lumen ───────────────────────────────────────────────
echo "--- Step 8: Creating Python venv and installing lumen ---"
# Export locked dependencies from uv.lock so the bundle gets exact pinned versions.
FROZEN_REQS="$(mktemp)"
(cd "$REPO_ROOT/pipeline" && uv export --frozen --no-hashes --no-emit-project > "$FROZEN_REQS")
python3 -m venv "$DIST_DIR/venv"
"$DIST_DIR/venv/bin/pip" install --quiet --upgrade pip
# pygraphviz needs graphviz C headers; auto-detect Homebrew/system paths
if command -v brew >/dev/null 2>&1; then
  GV_PREFIX="$(brew --prefix graphviz 2>/dev/null || true)"
  if [ -n "$GV_PREFIX" ] && [ -d "$GV_PREFIX/include" ]; then
    export CFLAGS="-I${GV_PREFIX}/include"
    export LDFLAGS="-L${GV_PREFIX}/lib"
  fi
fi
"$DIST_DIR/venv/bin/pip" install --quiet -r "$FROZEN_REQS"
"$DIST_DIR/venv/bin/pip" install --quiet --no-deps "$REPO_ROOT/pipeline/"
"$DIST_DIR/venv/bin/pip" install --quiet \
  -r "$REPO_ROOT/indexer/parsers/python/requirements.txt"
rm -f "$FROZEN_REQS"
# Strip cache/stubs that are not needed at runtime
find "$DIST_DIR/venv" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$DIST_DIR/venv" \( -name "*.pyc" -o -name "*.pyo" \) -delete 2>/dev/null || true
find "$DIST_DIR/venv" -name "*.pyi" -delete 2>/dev/null || true
echo "  ✓ venv/ ($(du -sh "$DIST_DIR/venv" | cut -f1))"

# ── Step 9: Pipeline scripts ──────────────────────────────────────────────────
echo "--- Step 9: Copying pipeline scripts ---"
cp -r "$REPO_ROOT/pipeline/scripts/." "$DIST_DIR/scripts/"
chmod +x "$DIST_DIR/scripts/"*.sh
echo "  ✓ scripts/"

# ── Step 10: Relocatable CMG wrapper scripts ──────────────────────────────────
echo "--- Step 10: Writing relocatable CMG wrappers ---"

# Each wrapper resolves its own location at runtime so the bundle can be
# placed anywhere without regenerating anything.
cat > "$DIST_DIR/bin/cmg-java" <<'WRAPPER'
#!/usr/bin/env bash
_D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_D/../jre/bin/java" -jar "$_D/../lib/code-mem-graph.jar" "$@"
WRAPPER

cat > "$DIST_DIR/bin/cmg-js" <<'WRAPPER'
#!/usr/bin/env bash
_D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_D/../node/bin/node" "$_D/../indexer/parsers/javascript/parse.js" "$@"
WRAPPER

cat > "$DIST_DIR/bin/cmg-python" <<'WRAPPER'
#!/usr/bin/env bash
_D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_D/../venv/bin/python" "$_D/../indexer/parsers/python/parse.py" "$@"
WRAPPER

cat > "$DIST_DIR/bin/plantuml" <<'WRAPPER'
#!/usr/bin/env bash
_D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_D/../jre/bin/java" -jar "$_D/../lib/plantuml.jar" "$@"
WRAPPER

# cmg-php: calls system PHP (PHP binary cannot be bundled); prepends bundled venv/bin to
# PATH so store.php::findPython() finds the bundled Python (with kuzu) not the system one.
if [ "$PHP_INCLUDED" -eq 1 ]; then
  cat > "$DIST_DIR/bin/cmg-php" <<'WRAPPER'
#!/usr/bin/env bash
_D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v php >/dev/null 2>&1; then
  echo "ERROR: PHP is not installed. PHP 8.0+ is required for PHP indexing." >&2
  echo "  macOS: brew install php" >&2
  echo "  Linux: sudo apt install php-cli  (or: sudo yum install php-cli)" >&2
  exit 1
fi
export PATH="$_D/../venv/bin:$PATH"
exec php -d memory_limit=1G "$_D/../indexer/parsers/php/parse.php" "$@"
WRAPPER
  chmod +x "$DIST_DIR/bin/cmg-php"
fi

chmod +x \
  "$DIST_DIR/bin/cmg-java" \
  "$DIST_DIR/bin/cmg-js" \
  "$DIST_DIR/bin/cmg-python" \
  "$DIST_DIR/bin/plantuml"

if [ "$PHP_INCLUDED" -eq 1 ]; then
  echo "  ✓ bin/cmg-java  bin/cmg-js  bin/cmg-python  bin/cmg-php  bin/plantuml"
else
  echo "  ✓ bin/cmg-java  bin/cmg-js  bin/cmg-python  bin/plantuml"
fi

# ── Step 11: lumen launcher ───────────────────────────────────────────────────
echo "--- Step 11: Writing lumen launcher ---"

# The launcher regenerates .codedoc.toml with the correct absolute paths every
# time it runs.  This mirrors Docker's /workspace/.codedoc.toml mechanism and
# means the bundle stays functional after being moved or renamed.
cat > "$DIST_DIR/lumen" <<'LAUNCHER'
#!/usr/bin/env bash
# lumen — self-contained launcher
# Resolves bundle root, writes .codedoc.toml with correct absolute paths,
# then delegates to the bundled Python venv entry point.
set -euo pipefail
# Resolve symlinks so BUNDLE_DIR is correct even when invoked via ~/.local/bin/lumen
_SCRIPT="${BASH_SOURCE[0]}"
while [ -L "$_SCRIPT" ]; do
  _DIR="$(cd "$(dirname "$_SCRIPT")" && pwd)"
  _SCRIPT="$(readlink "$_SCRIPT")"
  [[ "$_SCRIPT" != /* ]] && _SCRIPT="$_DIR/$_SCRIPT"
done
BUNDLE_DIR="$(cd "$(dirname "$_SCRIPT")" && pwd)"
INVOCATION_DIR="$(pwd)"  # capture before cd so output_dir is relative to where lumen was run

# Regenerate runtime config so config.py picks up correct indexer_bin_dir
# and build_script regardless of where the bundle lives on disk.
cat > "$BUNDLE_DIR/.codedoc.toml" << TOML
[paths]
indexer_bin_dir = "$BUNDLE_DIR/bin"
build_script    = "$BUNDLE_DIR/scripts/build-docs-site.sh"
TOML

# Put bundled bin/ first so plantuml and cmg-* wrappers shadow any system versions.
export PATH="$BUNDLE_DIR/bin:$PATH"

# Warn if graphviz is missing — needed for docs generation (PlantUML + pygraphviz).
if ! command -v dot >/dev/null 2>&1; then
  echo "WARNING: graphviz not found — docs generation may fail." >&2
  case "$(uname -s)" in
    Darwin) echo "  Install: brew install graphviz" >&2 ;;
    Linux)  echo "  Install: sudo apt install graphviz  (or: sudo yum install graphviz)" >&2 ;;
  esac
fi

# cd to bundle root so config.py's CWD-based .codedoc.toml lookup succeeds.
cd "$BUNDLE_DIR"

# Inject --output-dir pointing back to the invocation directory unless the
# caller already passed it. This mirrors Docker's ./output convention.
has_output_dir=0
for arg in "$@"; do
  [ "$arg" = "--output-dir" ] && has_output_dir=1 && break
done
if [ "$has_output_dir" -eq 0 ]; then
  exec "$BUNDLE_DIR/venv/bin/lumen" --output-dir "$INVOCATION_DIR/output" "$@"
else
  exec "$BUNDLE_DIR/venv/bin/lumen" "$@"
fi
LAUNCHER

chmod +x "$DIST_DIR/lumen"
echo "  ✓ lumen"

# ── Step 12: install.sh ───────────────────────────────────────────────────────
cat > "$DIST_DIR/install.sh" <<'INST'
#!/usr/bin/env bash
# Symlink the lumen launcher into a directory on your PATH.
# Usage: ./install.sh [target-dir]   (default: ~/.local/bin)
set -euo pipefail
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINK_DIR="${1:-$HOME/.local/bin}"
mkdir -p "$LINK_DIR"
ln -sf "$BUNDLE_DIR/lumen" "$LINK_DIR/lumen"
echo "Linked: $LINK_DIR/lumen -> $BUNDLE_DIR/lumen"
echo ""
echo "Make sure $LINK_DIR is in your PATH:"
echo "  export PATH=\"$LINK_DIR:\$PATH\""
INST

chmod +x "$DIST_DIR/install.sh"
echo "  ✓ install.sh"

# ── Step 13: Internal SHA256SUMS + verify.sh ──────────────────────────────────
echo "--- Step 13: Generating internal checksums ---"
(cd "$DIST_DIR" && {
  sha256_manifest=""
  for f in \
    lib/code-mem-graph.jar \
    lib/plantuml.jar \
    node/bin/node \
    lumen \
    bin/cmg-java \
    bin/cmg-js \
    bin/cmg-python \
    bin/cmg-php \
    bin/plantuml; do
    [ -f "$f" ] && sha256_manifest+="$(sha256_file "$f")  $f"$'\n'
  done
  # Include kuzu native addon if present
  for kuzufile in $(find indexer/parsers/javascript/node_modules -name "kuzujs.node" 2>/dev/null); do
    sha256_manifest+="$(sha256_file "$kuzufile")  $kuzufile"$'\n'
  done
  printf '%s' "$sha256_manifest" > SHA256SUMS
})
echo "  ✓ SHA256SUMS ($(wc -l < "$DIST_DIR/SHA256SUMS") entries)"

cat > "$DIST_DIR/verify.sh" <<'VERIFY'
#!/usr/bin/env bash
# Verify the integrity of all critical binaries in this bundle.
set -euo pipefail
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUMS_FILE="$BUNDLE_DIR/SHA256SUMS"
[ -f "$SUMS_FILE" ] || { echo "ERROR: SHA256SUMS not found in bundle"; exit 1; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

failed=0
while IFS='  ' read -r expected path; do
  [ -z "$expected" ] && continue
  full="$BUNDLE_DIR/$path"
  if [ ! -f "$full" ]; then
    echo "MISSING: $path"
    failed=1
    continue
  fi
  actual="$(sha256_file "$full")"
  if [ "$actual" = "$expected" ]; then
    echo "  OK: $path"
  else
    echo "  FAIL: $path"
    echo "    expected: $expected"
    echo "    actual:   $actual"
    failed=1
  fi
done < "$SUMS_FILE"

if [ "$failed" -eq 0 ]; then
  echo ""
  echo "All checksums verified."
else
  echo ""
  echo "ERROR: One or more checksums failed."
  exit 1
fi
VERIFY

chmod +x "$DIST_DIR/verify.sh"
echo "  ✓ verify.sh"

# ── Step 13: Package ──────────────────────────────────────────────────────────
echo "--- Step 13: Packaging ---"
mkdir -p "$RELEASES_DIR"
TARBALL="$RELEASES_DIR/${BUNDLE_NAME}.tar.gz"
tar -czf "$TARBALL" -C "$REPO_ROOT/dist" "$BUNDLE_NAME"
CHECKSUM="$(sha256_file "$TARBALL")"
echo "$CHECKSUM  ${BUNDLE_NAME}.tar.gz" > "${TARBALL}.sha256"

TARBALL_SIZE="$(du -sh "$TARBALL" | cut -f1)"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  lumen ${VERSION} — ${OS}-${ARCH}"
echo ""
echo "  Tarball:  releases/${BUNDLE_NAME}.tar.gz  (${TARBALL_SIZE})"
echo "  SHA256:   $CHECKSUM"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Quickstart:"
echo "  tar -xzf releases/${BUNDLE_NAME}.tar.gz"
echo "  ./${BUNDLE_NAME}/lumen run /path/to/repo \\"
echo "    --provider anthropic --model claude-sonnet-4-6"
echo ""
echo "Add to PATH:"
echo "  ./${BUNDLE_NAME}/install.sh"
echo ""
echo "NOTE: graphviz must be installed on the target machine for docs"
echo "  macOS: brew install graphviz"
echo "  Linux: apt install graphviz  (or: yum install graphviz)"
echo ""
