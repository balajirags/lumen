# ─────────────────────────────────────────────────────────────────────────────
# lumen — multi-stage Docker build
#
# Stage 1 (java-builder)   : Gradle shadowJar + jlink minimal JRE
# Stage 2 (python-builder) : PyInstaller → self-contained lumen + cmg-python
# Stage 3 (node-builder)   : npm install (production) for JS parser
# Stage 4 (final)          : node:20-slim + custom JRE + Python binaries
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


# ── Stage 2: PyInstaller — lumen CLI + cmg-python parser ─────────────────────
FROM python:3.11-slim AS python-builder

RUN pip install --no-cache-dir pyinstaller

# ── 2a: lumen CLI ──
WORKDIR /pipeline
COPY pipeline/ .

RUN pip install --no-cache-dir .

# Bundle: collect codedoc package (incl. prompt .md files) + kuzu native lib
RUN pyinstaller codedoc/cli.py \
      --name lumen \
      --onefile \
      --collect-all codedoc \
      --collect-all kuzu \
      --collect-all anthropic \
      --collect-all openai \
      --hidden-import dotenv \
      --clean -y

# ── 2b: cmg-python parser ──
WORKDIR /indexer-python
COPY indexer/parsers/python/ .

RUN pip install --no-cache-dir -r requirements.txt

RUN pyinstaller parse.py \
      --name cmg-python \
      --onefile \
      --collect-all kuzu \
      --clean -y


# ── Stage 3: Node parser dependencies ────────────────────────────────────────
FROM node:20-slim AS node-builder

WORKDIR /build
COPY indexer/parsers/javascript/ .

RUN npm install --omit=dev --silent


# ── Stage 4: Final slim image ─────────────────────────────────────────────────
# node:20-slim provides Node.js + npm (needed for cmg-js and Docusaurus builder)
FROM node:20-slim

# ── Custom Java JRE + fat JAR ──
COPY --from=java-builder /custom-jre                         /opt/jre
COPY --from=java-builder /build/app/build/libs/code-mem-graph.jar \
                                                             /opt/cmg/code-mem-graph.jar

# ── Python binaries ──
COPY --from=python-builder /pipeline/dist/lumen              /usr/local/bin/lumen
COPY --from=python-builder /indexer-python/dist/cmg-python   /usr/local/bin/cmg-python

# ── Node JS parser ──
COPY --from=node-builder /build                              /opt/cmg-js/

# ── Pipeline scripts (needed by Docusaurus builder stage) ──
COPY pipeline/scripts/                                       /opt/lumen/scripts/

# ── Wrapper scripts for indexer binaries ──
RUN printf '#!/bin/sh\nexec /opt/jre/bin/java -jar /opt/cmg/code-mem-graph.jar "$@"\n' \
      > /usr/local/bin/cmg-java && chmod +x /usr/local/bin/cmg-java

RUN printf '#!/bin/sh\nexec node /opt/cmg-js/parse.js "$@"\n' \
      > /usr/local/bin/cmg-js && chmod +x /usr/local/bin/cmg-js

RUN chmod +x /usr/local/bin/lumen /usr/local/bin/cmg-python \
             /opt/lumen/scripts/build-docs-site.sh

# ── Runtime config: override paths for the Docker environment ──
# load_config() reads .codedoc.toml from CWD (/workspace), so this file
# wires the pre-installed binaries and scripts into the pipeline.
RUN mkdir -p /workspace && printf '\
[paths]\n\
indexer_bin_dir = "/usr/local/bin"\n\
build_script    = "/opt/lumen/scripts/build-docs-site.sh"\n\
' > /workspace/.codedoc.toml

# Mount points: users bind-mount their repo at /repo and output at /workspace/output
WORKDIR /workspace

ENTRYPOINT ["lumen"]
CMD ["--help"]
