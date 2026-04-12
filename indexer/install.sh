#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

echo "==> Installing code-mem-graph..."
echo ""

mkdir -p "$BIN_DIR"

# --- Java / Kotlin (fat JAR) ---
echo "--- Building Java fat JAR ---"
if command -v java >/dev/null 2>&1; then
    "$SCRIPT_DIR/gradlew" -p "$SCRIPT_DIR" shadowJar --quiet
    JAR_PATH="$SCRIPT_DIR/app/build/libs/code-mem-graph.jar"
    if [ -f "$JAR_PATH" ]; then
        cat > "$BIN_DIR/cmg-java" <<EOF
#!/usr/bin/env bash
exec java -jar "$JAR_PATH" "\$@"
EOF
        chmod +x "$BIN_DIR/cmg-java"
        echo "  ✓ bin/cmg-java"
    else
        echo "  ✗ shadowJar build failed — JAR not found"
    fi
else
    echo "  ⚠ Java not found — skipping Java/Kotlin build"
fi

# --- JavaScript / TypeScript ---
echo "--- Setting up JavaScript parser ---"
if command -v node >/dev/null 2>&1; then
    (cd "$SCRIPT_DIR/parsers/javascript" && npm install --silent 2>/dev/null)
    cat > "$BIN_DIR/cmg-js" <<EOF
#!/usr/bin/env bash
NODE_ARGS=()
[ -n "\${CMG_JS_HEAP_MB:-}" ] && NODE_ARGS+=(--max-old-space-size="\$CMG_JS_HEAP_MB")
exec node "\${NODE_ARGS[@]}" "$SCRIPT_DIR/parsers/javascript/parse.js" "\$@"
EOF
    chmod +x "$BIN_DIR/cmg-js"
    echo "  ✓ bin/cmg-js"
else
    echo "  ⚠ Node.js not found — skipping JavaScript setup"
fi

# --- Python ---
echo "--- Setting up Python parser ---"
if command -v python3 >/dev/null 2>&1; then
    pip3 install -q -r "$SCRIPT_DIR/parsers/python/requirements.txt" 2>/dev/null || \
        python3 -m pip install -q -r "$SCRIPT_DIR/parsers/python/requirements.txt" 2>/dev/null || \
        echo "  ⚠ pip install failed — install dependencies manually: pip install -r parsers/python/requirements.txt"
    cat > "$BIN_DIR/cmg-python" <<EOF
#!/usr/bin/env bash
exec python3 "$SCRIPT_DIR/parsers/python/parse.py" "\$@"
EOF
    chmod +x "$BIN_DIR/cmg-python"
    echo "  ✓ bin/cmg-python"
else
    echo "  ⚠ Python 3 not found — skipping Python setup"
fi

echo ""
echo "==> Done! Executables created in: $BIN_DIR"
echo ""
echo "Usage:"
[ -f "$BIN_DIR/cmg-java" ]   && echo "  cmg-java   /path/to/java/project"
[ -f "$BIN_DIR/cmg-js" ]     && echo "  cmg-js     /path/to/react/app"
[ -f "$BIN_DIR/cmg-python" ] && echo "  cmg-python /path/to/flask/app"
echo ""
echo "Add to your PATH (optional):"
echo "  export PATH=\"$BIN_DIR:\$PATH\""
