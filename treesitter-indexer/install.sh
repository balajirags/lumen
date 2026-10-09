#!/usr/bin/env bash
set -euo pipefail

# Wholly independent install script -- mirrors the shape of
# indexer/install.sh's cmg-* stanzas but is self-contained and never touches
# anything under indexer/. Produces bin/cmg-java-ts, bin/cmg-python-ts,
# bin/cmg-php-ts, and bin/cmg-js-ts, dispatched to from
# pipeline/codedoc/stages/indexer.py when
# --experimental-treesitter-java/--experimental-treesitter-python/
# --experimental-treesitter-php/--experimental-treesitter-js is set.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "==> Installing treesitter-indexer..."
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

cat > "$BIN_DIR/cmg-python-ts" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python3" "$SCRIPT_DIR/parse_python.py" "\$@"
EOF
chmod +x "$BIN_DIR/cmg-python-ts"
echo "  ✓ bin/cmg-python-ts"

cat > "$BIN_DIR/cmg-php-ts" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python3" "$SCRIPT_DIR/parse_php.py" "\$@"
EOF
chmod +x "$BIN_DIR/cmg-php-ts"
echo "  ✓ bin/cmg-php-ts"

cat > "$BIN_DIR/cmg-js-ts" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python3" "$SCRIPT_DIR/parse_js.py" "\$@"
EOF
chmod +x "$BIN_DIR/cmg-js-ts"
echo "  ✓ bin/cmg-js-ts"
