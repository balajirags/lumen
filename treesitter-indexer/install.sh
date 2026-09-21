#!/usr/bin/env bash
set -euo pipefail

# Wholly independent install script -- mirrors the shape of
# indexer/install.sh's cmg-java stanza but is self-contained and never touches
# anything under indexer/. Produces bin/cmg-java-ts, a wrapper this repo's
# real pipeline (pipeline/codedoc/stages/indexer.py) never invokes -- see the
# plan's "Dispatch" section.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "==> Installing treesitter-indexer (Java prototype)..."
echo ""

mkdir -p "$BIN_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "  ✗ uv not found -- see https://docs.astral.sh/uv/ -- skipping treesitter-indexer build"
    exit 1
fi

echo "--- Syncing dependencies via uv (pyproject.toml / uv.lock) ---"
(cd "$SCRIPT_DIR" && uv sync)

cat > "$BIN_DIR/cmg-java-ts" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python3" "$SCRIPT_DIR/parse.py" "\$@"
EOF
chmod +x "$BIN_DIR/cmg-java-ts"
echo "  ✓ bin/cmg-java-ts"
