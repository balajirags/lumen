# ─────────────────────────────────────────────────────────────────────────────
# lumen — multi-stage Docker build
#
# Stage 1 (java-builder)      : Gradle shadowJar + jlink minimal JRE
# Stage 2 (node-builder)      : npm install (production) for JS parser
# Stage 3 (python-deps-builder): pip-compile all Python deps (incl. pygraphviz)
# Stage 4 (final)             : python:3.11-slim + JRE + Node binary + pip deps
#
# Using python:3.11-slim as the final base avoids GLIBC mismatches that occur
# when PyInstaller bundles are run on a different libc than they were built on.
# lumen runs directly as a pip-installed entry point — no PyInstaller needed.
#
# Build:
#   docker build -t lumen .
#
# Run:
#   docker run --rm \
#     -v /path/to/repo:/repo \
#     -v $(pwd)/output:/workspace/output \
#     -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
#     lumen run /repo --output-dir /workspace/output
# ─────────────────────────────────────────────────────────────────────────────


# ── Stage 1: Java fat JAR + custom minimal JRE via jlink ─────────────────────
FROM eclipse-temurin:21-jdk-jammy AS java-builder

WORKDIR /build
COPY indexer/ .

# Build the fat JAR (Gradle wrapper downloads Gradle on first run)
RUN ./gradlew shadowJar --no-daemon -q

# Download PlantUML JAR (used by mkdocs-build-plantuml-plugin at doc-build time)
RUN curl -fsSL -o /plantuml.jar \
    "https://github.com/plantuml/plantuml/releases/latest/download/plantuml.jar"

# Discover required modules, merge with a known-good baseline, then jlink
RUN JAR=app/build/libs/code-mem-graph.jar && \
    DETECTED=$(jdeps --ignore-missing-deps \
                     --print-module-deps \
                     --multi-release 21 \
                     "$JAR" 2>/dev/null || echo "") && \
    BASELINE="java.base,java.sql,java.logging,java.naming,java.management,\
java.net.http,java.security.jgss,java.security.sasl,java.xml,\
jdk.unsupported,jdk.crypto.ec" && \
    MODULES=$(printf '%s\n' "$DETECTED" "$BASELINE" | \
              tr ',' '\n' | grep -v '^$' | sort -u | paste -sd ',' -) && \
    jlink \
      --add-modules "$MODULES" \
      --strip-debug \
      --no-man-pages \
      --no-header-files \
      --output /custom-jre


# ── Stage 2: Node parser dependencies ────────────────────────────────────────
FROM node:20-slim AS node-builder

WORKDIR /build
COPY indexer/parsers/javascript/ .

RUN npm install --omit=dev --silent \
    && find node_modules -type d \( -name test -o -name tests -o -name __tests__ \
       -o -name example -o -name examples -o -name docs -o -name doc \) \
       -exec rm -rf {} + 2>/dev/null || true \
    && find node_modules -type f \( -name "*.md" -o -name "*.ts" -o -name "CHANGELOG*" \
       -o -name "*.map" \) -delete 2>/dev/null || true


# ── Stage 3: Python dependencies (compile wheels incl. pygraphviz) ───────────
FROM python:3.11-slim AS python-deps-builder

# uv — fast Python package installer (replaces pip)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Build tools needed to compile pygraphviz (python-graphs dependency)
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential graphviz libgraphviz-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install lumen pipeline (includes mkdocs-material, anthropic, openai, kuzu…)
COPY pipeline/ /opt/lumen-pipeline/
COPY indexer/parsers/python/requirements.txt /tmp/py-parser-requirements.txt

RUN uv pip install --no-cache-dir --prefix=/deps /opt/lumen-pipeline/ \
    && uv pip install --no-cache-dir --prefix=/deps -r /tmp/py-parser-requirements.txt \
    # Strip native libraries (kuzu ~150MB→80MB, tokenizers ~80MB→40MB, etc.)
    && find /deps -name "*.so" -o -name "*.so.*" \
       | xargs -r strip --strip-unneeded 2>/dev/null || true \
    # Remove test directories
    && find /deps -type d \( -name tests -o -name test -o -name testing \) \
       -exec rm -rf {} + 2>/dev/null || true \
    # Remove compiled Python cache files
    && find /deps -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
    && find /deps -name "*.pyc" -o -name "*.pyo" | xargs -r rm -f \
    # Remove type stubs (not needed at runtime)
    && find /deps -name "*.pyi" | xargs -r rm -f


# ── Stage 4: Final slim image ─────────────────────────────────────────────────
# python:3.11-slim matches the builder stage — same glibc, no GLIBC_* errors.
FROM python:3.11-slim

# Graphviz runtime libs (needed by pygraphviz at runtime; no -dev headers required)
RUN apt-get update && apt-get install -y --no-install-recommends \
      graphviz \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages (lumen + mkdocs-material + parser deps) ──
COPY --from=python-deps-builder /deps /usr/local

# ── Node.js binary (for cmg-js parser) ──
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node

# ── Custom Java JRE + fat JAR ──
COPY --from=java-builder /custom-jre                         /opt/jre
COPY --from=java-builder /build/app/build/libs/code-mem-graph.jar \
                                                             /opt/cmg/code-mem-graph.jar

# ── PlantUML JAR + wrapper script ──
COPY --from=java-builder /plantuml.jar                       /usr/local/bin/plantuml.jar
RUN printf '#!/bin/sh\nexec /opt/jre/bin/java -jar /usr/local/bin/plantuml.jar "$@"\n' \
      > /usr/local/bin/plantuml && chmod +x /usr/local/bin/plantuml

# ── Node JS parser ──
COPY --from=node-builder /build                              /opt/cmg-js/

# ── cmg-python parser source ──
COPY indexer/parsers/python/                                 /opt/cmg-python-src/

# ── Pipeline scripts ──
COPY pipeline/scripts/                                       /opt/lumen/scripts/
RUN chmod +x /opt/lumen/scripts/build-docs-site.sh

# ── Wrapper scripts + runtime config (single layer) ──
RUN printf '#!/bin/sh\nexec /opt/jre/bin/java -jar /opt/cmg/code-mem-graph.jar "$@"\n' \
      > /usr/local/bin/cmg-java \
    && printf '#!/bin/sh\nexec node /opt/cmg-js/parse.js "$@"\n' \
      > /usr/local/bin/cmg-js \
    && printf '#!/bin/sh\nexec python /opt/cmg-python-src/parse.py "$@"\n' \
      > /usr/local/bin/cmg-python \
    && chmod +x /usr/local/bin/cmg-java /usr/local/bin/cmg-js /usr/local/bin/cmg-python \
    && mkdir -p /workspace && printf '\
[paths]\n\
indexer_bin_dir = "/usr/local/bin"\n\
build_script    = "/opt/lumen/scripts/build-docs-site.sh"\n\
' > /workspace/.codedoc.toml

WORKDIR /workspace

ENTRYPOINT ["lumen"]
CMD ["--help"]
