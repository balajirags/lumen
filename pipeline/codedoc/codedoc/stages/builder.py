"""Stage 3 — Builder node.

Takes the artifact .md files and produces a navigable documentation site
using the existing build-docs-site.sh script, or falls back to a bare
HTML index page.

Usage (standalone CLI):
    python builder.py --artifacts-dir ./artifacts --repo-name my-service
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from codedoc.state import PipelineState


def run_builder(state: PipelineState) -> PipelineState:
    """LangGraph node: build documentation site from artifacts.

    Reads ``state.artifacts_dir``.
    Writes ``state.site_path``.
    
    Site is created at the same level as the codedoc folder (project root).
    If the site already exists, it will reuse it and just copy new artifacts.
    """
    stage = "builder"
    artifacts = Path(state.artifacts_dir)

    # Determine site directory
    if state.site_dir:
        site_dir = Path(state.site_dir)
    else:
        project_root = Path(__file__).parent.parent.parent
        site_dir = project_root / "doc-site"
    state.site_path = str(site_dir)

    build_script = Path(state.build_script)
    repo_name = Path(state.repo_path).name

    if not build_script.exists():
        site_dir.mkdir(parents=True, exist_ok=True)
        state.log(stage, f"Build script not found at {build_script}, using fallback HTML.")
        _generate_fallback_html(artifacts, site_dir, state)
        return state

    # --- Stage artifacts into a temp dir for build-docs-site.sh ---
    # The script expects <output-dir>/<repo-name>/<section>/ structure.
    # Use a temp dir so we don't pollute the source artifacts directory.
    staging_dir = Path(tempfile.mkdtemp(prefix="codedoc-build-"))
    staging_repo = staging_dir / repo_name
    staging_repo.mkdir()

    for src_file in artifacts.rglob("*"):
        if src_file.is_file():
            rel = src_file.relative_to(artifacts)
            dest = staging_repo / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src_file.suffix == ".md":
                dest.write_text(_sanitize_md_for_mdx(src_file.read_text()))
            else:
                dest.write_bytes(src_file.read_bytes())

    state.log(stage, f"Staged artifacts to {staging_repo}")

    # --- Run build script ---
    site_exists = (site_dir / "node_modules").exists()
    if site_exists:
        state.log(stage, f"Site already exists at {site_dir}, reusing.")

    cmd = [
        "bash",
        str(build_script),
        "--output-dir", str(staging_dir),
        "--site-dir", str(site_dir),
        "--title", f"{repo_name} — Forward Engineering Docs",
    ]

    state.log(stage, f"Running build script: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=state.timeout,
        )
    except subprocess.TimeoutExpired:
        state.log(stage, f"Build script timed out after {state.timeout}s, using fallback.")
        _generate_fallback_html(artifacts, site_dir, state)
        return state
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    if state.verbose:
        if proc.stdout:
            state.log(stage, f"stdout:\n{proc.stdout}")
        if proc.stderr:
            state.log(stage, f"stderr:\n{proc.stderr}")

    if proc.returncode != 0:
        stderr_tail = "\n".join(proc.stderr.strip().splitlines()[-20:]) if proc.stderr else "(no stderr)"
        state.log(stage, f"Build script failed (exit {proc.returncode}), using fallback.\n{stderr_tail}")
        _generate_fallback_html(artifacts, site_dir, state)
        return state

    # Check for index.html
    expected_index = site_dir / "build" / "index.html"
    if expected_index.exists():
        state.site_path = str(expected_index.parent)
        state.log(stage, f"Site built successfully at {state.site_path}")
    else:
        state.log(stage, "index.html not found after build, using fallback.")
        _generate_fallback_html(artifacts, site_dir, state)

    return state


_ANGLE_OUTSIDE_CODE = re.compile(
    r"(?P<fence>^```.*?^```)|(?P<inline>`[^`]+`)|(?P<bare><)"  ,
    re.MULTILINE | re.DOTALL,
)


def _sanitize_md_for_mdx(text: str) -> str:
    """Escape bare angle brackets outside code blocks/spans so MDX doesn't treat them as JSX."""
    def _replace(m: re.Match) -> str:
        if m.group("fence") or m.group("inline"):
            return m.group(0)  # keep code untouched
        return "&lt;"
    return _ANGLE_OUTSIDE_CODE.sub(_replace, text)


def _generate_fallback_html(artifacts_dir: Path, site_dir: Path, state: PipelineState) -> None:
    """Generate a bare HTML index linking to each artifact."""
    site_dir.mkdir(parents=True, exist_ok=True)
    stage = "builder"
    md_files = sorted(artifacts_dir.rglob("*.md"))

    links = []
    for md in md_files:
        rel = md.relative_to(artifacts_dir)
        links.append(f'    <li><a href="artifacts/{rel}">{rel}</a></li>')

    repo_name = Path(state.repo_path).name
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{repo_name} — Forward Engineering Docs</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.5rem; }}
    li {{ margin: 0.5rem 0; }}
    a {{ color: #0066cc; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>{repo_name} — Forward Engineering Artifacts</h1>
  <p>Generated by <code>codedoc</code> pipeline.</p>
  <h2>Artifacts</h2>
  <ul>
{chr(10).join(links) if links else '    <li>(no artifacts found)</li>'}
  </ul>
</body>
</html>"""

    index_path = site_dir / "index.html"
    index_path.write_text(html)

    # Symlink artifacts so relative links work
    artifacts_link = site_dir / "artifacts"
    if not artifacts_link.exists():
        artifacts_link.symlink_to(artifacts_dir.resolve())

    state.site_path = str(site_dir)
    state.log(stage, f"Fallback HTML index generated at {index_path}")


# ---------------------------------------------------------------------------
# Standalone CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run builder as standalone CLI."""
    parser = argparse.ArgumentParser(
        description="Build documentation site from artifacts."
    )
    parser.add_argument("--artifacts-dir", required=True, help="Path to artifacts directory")
    parser.add_argument("--repo-name", required=True, help="Repository/service name")
    parser.add_argument("--output-dir", default="", help="Output directory (optional, for temp files)")
    parser.add_argument("--build-script", default="./scripts/build-docs-site.sh", help="Path to build-docs-site.sh")
    parser.add_argument("--site-dir", default="", help="Site directory (default: <project-root>/doc-site)")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Find project root
    project_root = Path(__file__).parent.parent.parent

    # Determine site directory
    site_dir = args.site_dir if args.site_dir else str(project_root / "doc-site")

    # Create a minimal PipelineState
    state = PipelineState(
        repo_path=args.repo_name,
        output_dir=args.output_dir or str(Path(args.artifacts_dir).parent),
        artifacts_dir=args.artifacts_dir,
        build_script=args.build_script,
        site_dir=site_dir,
        timeout=args.timeout,
        verbose=args.verbose,
        status="running",
    )

    result = run_builder(state)
    
    print(f"\nStatus: {result.status}")
    print(f"Site path: {result.site_path}")
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
