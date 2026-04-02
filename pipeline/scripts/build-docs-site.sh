#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# build-docs-site.sh
#
# Creates a Docusaurus site from forward-engineering artifacts, wires
# up OpenAPI specs, generates API doc pages, and produces a static
# build — all in one shot.
#
# Usage:
#   ./build-docs-site.sh [--output-dir ./output] [--site-dir ./site/docs-site] [--title "My Docs"]
#
# Flags:
#   --output-dir   Path to the forward-engineering output directory (default: ./output)
#   --site-dir     Where the Docusaurus project will be created (default: ./site/docs-site)
#   --title        Title shown in the Docusaurus navbar (default: "Forward Engineering Docs")
#   --skip-build   Only scaffold + generate; skip the final production build
#   --clean        Remove generated docs/apis content before re-copying (keeps the site scaffold)
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────
OUTPUT_DIR="./output"
SITE_DIR="./site/docs-site"
SITE_TITLE="Forward Engineering Docs"
SKIP_BUILD=false
CLEAN=false

# ── Parse args ──────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)  OUTPUT_DIR="$2";  shift 2 ;;
    --site-dir)    SITE_DIR="$2";    shift 2 ;;
    --title)       SITE_TITLE="$2";  shift 2 ;;
    --skip-build)  SKIP_BUILD=true;  shift   ;;
    --clean)       CLEAN=true;       shift   ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# ── Validate ────────────────────────────────────────────────────────
if [[ ! -d "$OUTPUT_DIR" ]]; then
  echo "ERROR: Output directory '$OUTPUT_DIR' does not exist."
  exit 1
fi

# Resolve to absolute paths for safety
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

# ── Step 1: Create Docusaurus project (once) ──────────────────────
echo "══════════════════════════════════════════════════════════════"
echo "Step 1/6: Creating Docusaurus project at $SITE_DIR"
echo "══════════════════════════════════════════════════════════════"

SITE_PARENT="$(dirname "$SITE_DIR")"
SITE_NAME="$(basename "$SITE_DIR")"
mkdir -p "$SITE_PARENT"

if [[ ! -d "$SITE_DIR/node_modules" ]]; then
  npx create-docusaurus@latest "$SITE_DIR" classic --typescript
else
  echo "Docusaurus project already exists, skipping creation."
fi

# Resolve SITE_DIR to absolute after creation
SITE_DIR="$(cd "$SITE_DIR" && pwd)"

# When --clean, only remove generated docs/apis content — never the site scaffold
if $CLEAN; then
  echo "Cleaning generated docs and API content..."
  rm -rf "$SITE_DIR/docs" "$SITE_DIR/apis" "$SITE_DIR/build"
fi

# ── Step 2: Install OpenAPI plugin + polyfills ─────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo "Step 2/6: Installing OpenAPI plugin and polyfills"
echo "══════════════════════════════════════════════════════════════"

cd "$SITE_DIR"
# Only install if not already present
if [[ ! -d "node_modules/docusaurus-plugin-openapi-docs" ]]; then
  npm install docusaurus-plugin-openapi-docs docusaurus-theme-openapi-docs path-browserify process
else
  echo "OpenAPI plugin already installed, skipping."
fi

# ── Step 3: Copy artifacts ─────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo "Step 3/6: Copying forward-engineering artifacts"
echo "══════════════════════════════════════════════════════════════"

DOCS_DIR="$SITE_DIR/docs"
APIS_DIR="$SITE_DIR/apis"

# Remove boilerplate docs from create-docusaurus scaffold
rm -rf "$DOCS_DIR/tutorial-basics" "$DOCS_DIR/tutorial-extras" "$SITE_DIR/blog"
rm -f "$DOCS_DIR/intro.md"

# Iterate repo-name subdirectories under output/
# Expected structure: output/<repo-name>/<section>/
for repo_dir in "$OUTPUT_DIR"/*/; do
  [[ -d "$repo_dir" ]] || continue
  repo_name="$(basename "$repo_dir")"

  # Markdown doc sections — copy every subdirectory except openapi and manifests
  for section_dir in "$repo_dir"/*/; do
    [[ -d "$section_dir" ]] || continue
    section_name="$(basename "$section_dir")"
    # Skip openapi (handled separately) and manifests (not docs)
    [[ "$section_name" == "openapi" || "$section_name" == "manifests" ]] && continue
    mkdir -p "$DOCS_DIR/$repo_name/$section_name"
    rsync -av "$section_dir" "$DOCS_DIR/$repo_name/$section_name/"
  done

  # OpenAPI specs for this repo
  if [[ -d "$repo_dir/openapi" ]]; then
    mkdir -p "$APIS_DIR/$repo_name"
    rsync -av "$repo_dir/openapi/" "$APIS_DIR/$repo_name/"
  fi
done

# Landing page — auto-discover repos and their sections from what was copied
{
  cat << 'INTRO_HEADER'
---
sidebar_position: 1
slug: /
---

# Forward Engineering Documentation

Welcome to the auto-generated forward engineering documentation site.

INTRO_HEADER

  # List each repo and its sections
  for repo_dir in "$DOCS_DIR"/*/; do
    [[ -d "$repo_dir" ]] || continue
    repo_name="$(basename "$repo_dir")"
    repo_display="$(echo "$repo_name" | sed 's/-/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"

    echo "## ${repo_display}"
    echo ""
    echo "| Section | Path |"
    echo "|---------|------|"

    for section_dir in "$repo_dir"/*/; do
      [[ -d "$section_dir" ]] || continue
      section_name="$(basename "$section_dir")"
      display_name="$(echo "$section_name" | sed 's/-/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"
      echo "| **${display_name}** | [\`${repo_name}/${section_name}/\`](/docs/${repo_name}/${section_name}) |"
    done

    # Add API Reference row if specs exist for this repo
    if [[ -d "$APIS_DIR/$repo_name" ]] && ls "$APIS_DIR/$repo_name"/*.openapi.yaml &>/dev/null; then
      echo "| **API Reference** | Interactive OpenAPI documentation for target services |"
    fi
    echo ""
  done
} > "$DOCS_DIR/intro.md"

# Replace default homepage to redirect to docs
mkdir -p "$SITE_DIR/src/pages"
cat > "$SITE_DIR/src/pages/index.tsx" << 'PAGE_EOF'
import { Redirect } from "@docusaurus/router";

export default function Home(): JSX.Element {
  return <Redirect to="/docs" />;
}
PAGE_EOF

# Default custom CSS
mkdir -p "$SITE_DIR/src/css"
cat > "$SITE_DIR/src/css/custom.css" << 'CSS_EOF'
:root {
  --ifm-color-primary: #2e8555;
  --ifm-color-primary-dark: #29784c;
  --ifm-color-primary-darker: #277148;
  --ifm-color-primary-darkest: #205d3b;
  --ifm-color-primary-light: #33925d;
  --ifm-color-primary-lighter: #359962;
  --ifm-color-primary-lightest: #3cad6e;
  --ifm-code-font-size: 95%;
  --docusaurus-highlighted-code-line-bg: rgba(0, 0, 0, 0.1);
}

[data-theme='dark'] {
  --ifm-color-primary: #25c2a0;
  --ifm-color-primary-dark: #21af90;
  --ifm-color-primary-darker: #1fa588;
  --ifm-color-primary-darkest: #1a8870;
  --ifm-color-primary-light: #29d5b0;
  --ifm-color-primary-lighter: #32d8b4;
  --ifm-color-primary-lightest: #4fddbf;
  --docusaurus-highlighted-code-line-bg: rgba(0, 0, 0, 0.3);
}
CSS_EOF

# ── Step 4: Generate docusaurus.config.ts ──────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
echo "Step 4/6: Generating Docusaurus configuration"
echo "══════════════════════════════════════════════════════════════"

# Discover OpenAPI specs across all repo subdirectories and build plugin config entries
OPENAPI_CONFIG=""
SPEC_COUNT=0

if [[ -d "$APIS_DIR" ]]; then
  for repo_apis_dir in "$APIS_DIR"/*/; do
    [[ -d "$repo_apis_dir" ]] || continue
    repo_name="$(basename "$repo_apis_dir")"

    for spec_file in "$repo_apis_dir"/*.openapi.yaml; do
      [[ -f "$spec_file" ]] || continue
      SPEC_COUNT=$((SPEC_COUNT + 1))

      # Derive a camelCase key and output dir name from filename
      base_name="$(basename "$spec_file" .openapi.yaml)"
      # Prefix with repo name for uniqueness, convert to camelCase
      unique_key="${repo_name}-${base_name}"
      camel_key="$(echo "$unique_key" | sed -E 's/-([a-z])/\U\1/g')"

      OPENAPI_CONFIG+="
          ${camel_key}: {
            specPath: 'apis/${repo_name}/${base_name}.openapi.yaml',
            outputDir: 'docs/${repo_name}/api/${base_name}',
          } satisfies OpenApiPlugin.Options,"
    done
  done
fi

# Build the openapi imports + plugin + theme blocks conditionally
if [[ $SPEC_COUNT -gt 0 ]]; then
  OPENAPI_IMPORT="import type * as OpenApiPlugin from 'docusaurus-plugin-openapi-docs';"
  OPENAPI_DOC_ITEM="docItemComponent: '@theme/ApiItem',"
  OPENAPI_PLUGIN="
    [
      'docusaurus-plugin-openapi-docs',
      {
        id: 'api',
        docsPluginId: 'classic',
        config: {${OPENAPI_CONFIG}
        },
      },
    ],"
  OPENAPI_POLYFILL="
    function polyfillPlugin() {
      return {
        name: 'node-polyfill-plugin',
        configureWebpack() {
          return {
            resolve: {
              fallback: {
                path: require.resolve('path-browserify'),
                fs: false,
              },
            },
            plugins: [
              new webpack.ProvidePlugin({
                process: 'process/browser.js',
              }),
            ],
          };
        },
      };
    },"
  OPENAPI_WEBPACK_IMPORT="import webpack from 'webpack';"
  OPENAPI_THEME="
  themes: ['docusaurus-theme-openapi-docs'],"
else
  OPENAPI_IMPORT=""
  OPENAPI_DOC_ITEM=""
  OPENAPI_PLUGIN=""
  OPENAPI_POLYFILL=""
  OPENAPI_WEBPACK_IMPORT=""
  OPENAPI_THEME=""
fi

cat > "$SITE_DIR/docusaurus.config.ts" << CONFIG_EOF
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';
${OPENAPI_IMPORT}
${OPENAPI_WEBPACK_IMPORT}

const config: Config = {
  title: '${SITE_TITLE}',
  url: 'https://example.com',
  baseUrl: '/',
  favicon: 'img/favicon.ico',
  organizationName: 'your-org',
  projectName: 'forward-engineering-docs',
  onBrokenLinks: 'warn',

  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          ${OPENAPI_DOC_ITEM}
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  plugins: [${OPENAPI_PLUGIN}${OPENAPI_POLYFILL}
  ],
${OPENAPI_THEME}
  themeConfig: {
    navbar: {
      title: '${SITE_TITLE}',
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'tutorialSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/your-org/forward-engineering-docs',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      copyright: 'Copyright © ' + new Date().getFullYear() + ' Forward Engineering Docs. Built with Docusaurus.',
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
CONFIG_EOF

# Sidebars: auto-generated from directory structure
cat > "$SITE_DIR/sidebars.ts" << 'SIDEBAR_EOF'
import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  tutorialSidebar: [{type: 'autogenerated', dirName: '.'}],
};

export default sidebars;
SIDEBAR_EOF

echo "  Config generated with $SPEC_COUNT OpenAPI spec(s)."

# ── Step 5: Generate API doc pages from OpenAPI specs ──────────────
if [[ $SPEC_COUNT -gt 0 ]]; then
  echo ""
  echo "══════════════════════════════════════════════════════════════"
  echo "Step 5/6: Generating API docs from OpenAPI specs"
  echo "══════════════════════════════════════════════════════════════"

  npx docusaurus gen-api-docs all
else
  echo ""
  echo "══════════════════════════════════════════════════════════════"
  echo "Step 5/6: No OpenAPI specs found, skipping API doc generation"
  echo "══════════════════════════════════════════════════════════════"
fi

# ── Step 6: Build ──────────────────────────────────────────────────
if $SKIP_BUILD; then
  echo ""
  echo "══════════════════════════════════════════════════════════════"
  echo "Step 6/6: Skipping build (--skip-build)"
  echo "══════════════════════════════════════════════════════════════"
  echo "Run 'cd $SITE_DIR && npm run build' to build later."
  echo "Run 'cd $SITE_DIR && npm run serve' to preview."
else
  echo ""
  echo "══════════════════════════════════════════════════════════════"
  echo "Step 6/6: Building production site"
  echo "══════════════════════════════════════════════════════════════"

  npm run build

  echo ""
  echo "════════════════════════════════════════════════════════════=="
  echo "Done! Static site generated at: $SITE_DIR/build"
  echo "Preview with: cd $SITE_DIR && npm run serve"
  echo "════════════════════════════════════════════════════════════=="
fi
