#!/usr/bin/env bash
# bump-version.sh — Update version across all project files.
# Usage: bash scripts/bump-version.sh <version>
#   e.g. bash scripts/bump-version.sh 0.2.0
#   e.g. bash scripts/bump-version.sh v0.2.0   (leading v is stripped)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PYPROJECT="$ROOT_DIR/pipeline/pyproject.toml"
PACKAGE_JSON="$ROOT_DIR/indexer/parsers/javascript/package.json"

# --- Parse argument ---
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <version>" >&2
  echo "  e.g. $0 0.2.0" >&2
  exit 1
fi

VERSION="${1#v}"  # strip leading v

# --- Validate format (semver: MAJOR.MINOR.PATCH with optional pre-release) ---
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
  echo "Error: Invalid version format '$VERSION'. Expected MAJOR.MINOR.PATCH (e.g. 0.2.0)" >&2
  exit 1
fi

echo "Bumping version to $VERSION ..."

# --- 1. Update pyproject.toml ---
if ! grep -q '^version = "' "$PYPROJECT"; then
  echo "Error: Could not find 'version = \"...\"' in $PYPROJECT" >&2
  exit 1
fi
sed -i '' "s/^version = \".*\"/version = \"$VERSION\"/" "$PYPROJECT"
echo "  ✓ pipeline/pyproject.toml"

# --- 2. Update package.json ---
if ! grep -q '"version":' "$PACKAGE_JSON"; then
  echo "Error: Could not find '\"version\":' in $PACKAGE_JSON" >&2
  exit 1
fi
# Use Python for reliable JSON editing (preserves formatting better than sed for JSON)
python3 -c "
import json, pathlib
p = pathlib.Path('$PACKAGE_JSON')
data = json.loads(p.read_text())
data['version'] = '$VERSION'
p.write_text(json.dumps(data, indent=2) + '\n')
"
echo "  ✓ indexer/parsers/javascript/package.json"

# --- 3. Regenerate uv.lock ---
echo "  Regenerating uv.lock ..."
(cd "$ROOT_DIR/pipeline" && uv lock --quiet)
echo "  ✓ pipeline/uv.lock"

# --- 4. Validate all files match ---
PY_VER=$(grep '^version = "' "$PYPROJECT" | sed 's/version = "\(.*\)"/\1/')
JS_VER=$(python3 -c "import json; print(json.load(open('$PACKAGE_JSON'))['version'])")
UV_VER=$(grep -A1 'name = "lumen"' "$ROOT_DIR/pipeline/uv.lock" | grep 'version' | sed 's/.*"\(.*\)".*/\1/')

MISMATCH=0
if [[ "$PY_VER" != "$VERSION" ]]; then
  echo "Error: pyproject.toml has $PY_VER, expected $VERSION" >&2
  MISMATCH=1
fi
if [[ "$JS_VER" != "$VERSION" ]]; then
  echo "Error: package.json has $JS_VER, expected $VERSION" >&2
  MISMATCH=1
fi
if [[ "$UV_VER" != "$VERSION" ]]; then
  echo "Error: uv.lock has $UV_VER, expected $VERSION" >&2
  MISMATCH=1
fi

if [[ $MISMATCH -eq 1 ]]; then
  echo "Version mismatch detected — aborting." >&2
  exit 1
fi

echo ""
echo "All files updated to $VERSION:"
echo "  pipeline/pyproject.toml"
echo "  indexer/parsers/javascript/package.json"
echo "  pipeline/uv.lock"
echo ""
echo "Review changes, then run:  make release VERSION=$VERSION"
