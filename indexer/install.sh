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
    (cd "$SCRIPT_DIR/parsers/javascript" && npm ci --omit=dev --silent 2>/dev/null)
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

# --- PHP ---
echo "--- Setting up PHP parser ---"
if command -v php >/dev/null 2>&1; then
    # Prefer already-installed vendor dir (avoid requiring composer at runtime)
    if [ -d "$SCRIPT_DIR/parsers/php/vendor" ]; then
        : # vendor already present from a previous composer install
    elif command -v composer >/dev/null 2>&1; then
        (cd "$SCRIPT_DIR/parsers/php" && composer install --no-dev --quiet 2>/dev/null) || \
            echo "  ⚠ composer install failed — install manually: cd parsers/php && composer install"
    else
        # Try common Homebrew / user-local paths
        COMPOSER_BIN=""
        for _p in /opt/homebrew/bin/composer /usr/local/bin/composer "$HOME/.local/bin/composer"; do
            [ -f "$_p" ] && { COMPOSER_BIN="$_p"; break; }
        done
        if [ -n "$COMPOSER_BIN" ]; then
            (cd "$SCRIPT_DIR/parsers/php" && php "$COMPOSER_BIN" install --no-dev --quiet 2>/dev/null) || \
                echo "  ⚠ composer install failed — install manually: cd parsers/php && composer install"
        else
            echo "  ⚠ Composer not found — install from https://getcomposer.org and re-run (or pre-install vendor/)"
        fi
    fi
    cat > "$BIN_DIR/cmg-php" <<EOF
#!/usr/bin/env bash
exec php -d memory_limit=1G "$SCRIPT_DIR/parsers/php/parse.php" "\$@"
EOF
    chmod +x "$BIN_DIR/cmg-php"
    echo "  ✓ bin/cmg-php"
else
    echo "  ⚠ PHP not found — skipping PHP setup"
fi

echo ""
echo "==> Done! Executables created in: $BIN_DIR"
echo ""
echo "Usage:"
[ -f "$BIN_DIR/cmg-java" ]   && echo "  cmg-java   /path/to/java/project"
[ -f "$BIN_DIR/cmg-js" ]     && echo "  cmg-js     /path/to/react/app"
[ -f "$BIN_DIR/cmg-python" ] && echo "  cmg-python /path/to/flask/app"
[ -f "$BIN_DIR/cmg-php" ]    && echo "  cmg-php    /path/to/laravel/app"
echo ""
echo "Add to your PATH (optional):"
echo "  export PATH=\"$BIN_DIR:\$PATH\""
