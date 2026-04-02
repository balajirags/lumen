"""Stage 1 — Indexer node.

Wraps the existing cmg-* binaries to produce a KuzuDB graph from a repo.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from codedoc.state import PipelineState


# File-extension heuristics for language detection
_JAVA_EXTS = {".java", ".kt", ".kts"}
_JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_PY_EXTS = {".py", ".pyi"}


def detect_language(repo_path: str) -> str:
    """Guess dominant language by scanning file extensions.

    Returns one of: ``java``, ``js``, ``python``.
    Raises ``ValueError`` if no supported files found.
    """
    counts = {"java": 0, "js": 0, "python": 0}
    for root, _dirs, files in os.walk(repo_path):
        # skip hidden dirs and common non-source dirs
        parts = Path(root).parts
        if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv", "build", "dist", "target") for p in parts):
            continue
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in _JAVA_EXTS:
                counts["java"] += 1
            elif ext in _JS_EXTS:
                counts["js"] += 1
            elif ext in _PY_EXTS:
                counts["python"] += 1

    if all(v == 0 for v in counts.values()):
        raise ValueError(
            f"No supported source files found in {repo_path}. "
            "Expected Java/Kotlin, JavaScript/TypeScript, or Python files."
        )
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def _count_languages(repo_path: str) -> dict[str, int]:
    """Return file counts per language for the repo."""
    counts: dict[str, int] = {"java": 0, "js": 0, "python": 0}
    for root, _dirs, files in os.walk(repo_path):
        parts = Path(root).parts
        if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv", "build", "dist", "target") for p in parts):
            continue
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in _JAVA_EXTS:
                counts["java"] += 1
            elif ext in _JS_EXTS:
                counts["js"] += 1
            elif ext in _PY_EXTS:
                counts["python"] += 1
    return counts


_LANG_DISPLAY = {"java": "Java/Kotlin", "js": "JavaScript/TypeScript", "python": "Python"}


def _warn_secondary_languages(state: "PipelineState", stage: str, repo_path: str, dominant: str) -> None:
    """Log a warning if any secondary language has ≥20% of the dominant language's file count."""
    counts = _count_languages(repo_path)
    dominant_count = counts[dominant]
    if dominant_count == 0:
        return
    threshold = max(1, int(dominant_count * 0.20))
    secondaries = [
        f"{_LANG_DISPLAY[lang]} ({cnt:,} files)"
        for lang, cnt in counts.items()
        if lang != dominant and cnt >= threshold
    ]
    if secondaries:
        state.log(
            stage,
            f"WARNING: repo also contains significant {', '.join(secondaries)} in addition to "
            f"dominant {_LANG_DISPLAY[dominant]} ({dominant_count:,} files). "
            "Only the dominant language will be indexed.",
        )


_BINARY_MAP = {
    "java": "cmg-java",
    "js": "cmg-js",
    "python": "cmg-python",
}

# CLI flag for the KuzuDB path (unified across all binaries)
_DB_FLAG = "--db-path"


def run_indexer(state: PipelineState) -> PipelineState:
    """LangGraph node: index the repository into KuzuDB.

    Reads ``state.repo_path`` and ``state.output_dir``.
    Writes ``state.kuzu_path``.
    """
    repo = Path(state.repo_path).resolve()
    stage = "indexer"

    # --- Validate repo path ---
    if not repo.exists() or not repo.is_dir():
        raise FileNotFoundError(f"Repository path does not exist or is not a directory: {repo}")
    if not any(repo.iterdir()):
        raise ValueError(f"Repository directory is empty: {repo}")

    state.log(stage, f"Scanning {repo} for source files...")
    language = detect_language(str(repo))
    state.log(stage, f"Detected dominant language: {language}")

    # Warn if secondary languages are significant (≥20% of dominant)
    _warn_secondary_languages(state, stage, str(repo), language)

    binary_name = _BINARY_MAP[language]
    binary = Path(state.indexer_bin_dir) / binary_name
    if not binary.exists():
        raise FileNotFoundError(
            f"Indexer binary not found: {binary}\n"
            f"Run the install script or check your indexer_bin_dir setting."
        )

    # Create index.kuzu directory and place DB file inside it
    kuzu_dir = Path(state.output_dir).resolve() / "index.kuzu"
    kuzu_dir.mkdir(parents=True, exist_ok=True)
    kuzu_path = str(kuzu_dir / "db")
    state.log(stage, f"Running {binary_name} → {kuzu_path}")

    # --- Execute indexer subprocess ---
    # All indexer binaries now honor --db-path directly.
    cmd = [str(binary), str(repo), _DB_FLAG, kuzu_path]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=state.timeout,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(
            f"Indexer timed out after {state.timeout}s. "
            "Try increasing --timeout or indexing a smaller repo."
        )

    if state.verbose:
        if proc.stdout:
            state.log(stage, f"stdout:\n{proc.stdout}")
        if proc.stderr:
            state.log(stage, f"stderr:\n{proc.stderr}")

    if proc.returncode != 0:
        lines = proc.stderr.strip().splitlines() if proc.stderr else []
        if not lines:
            stderr_excerpt = "(no stderr)"
        elif len(lines) <= 20:
            stderr_excerpt = "\n".join(lines)
        else:
            head = "\n".join(lines[:10])
            tail = "\n".join(lines[-10:])
            stderr_excerpt = f"{head}\n[... {len(lines) - 20} lines omitted ...]\n{tail}"
        raise RuntimeError(
            f"Indexer exited with code {proc.returncode}.\n{stderr_excerpt}"
        )

    # --- Validate output ---
    kuzu = Path(kuzu_path)
    if not kuzu.exists():
        state.log(stage, f"stdout:\n{proc.stdout[-1000:]}" if proc.stdout else "No stdout")
        raise FileNotFoundError(
            f"Indexer completed but KuzuDB output not found at {kuzu_path}. "
            "Check the repo contents and indexer output."
        )
    # KuzuDB creates a directory, check it has content
    if kuzu.is_dir():
        total_size = sum(f.stat().st_size for f in kuzu.rglob("*") if f.is_file())
    else:
        total_size = kuzu.stat().st_size
    if total_size < 1024:
        raise ValueError(
            f"KuzuDB output at {kuzu_path} is only {total_size} bytes — "
            "too small to be a valid graph. Check the repo contents."
        )

    state.kuzu_path = kuzu_path
    state.log(stage, f"Indexing complete. KuzuDB at {kuzu_path} ({total_size:,} bytes)")
    return state


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the indexer standalone (outside the full pipeline)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Index a source repo into a KuzuDB code property graph."
    )
    parser.add_argument("repo_path", help="Path to the source code repository")
    parser.add_argument("--output-dir", default="./codedoc-output",
                        help="Output directory (default: ./codedoc-output)")
    parser.add_argument("--bin-dir", default="",
                        help="Directory containing cmg-* binaries")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Timeout in seconds (default: 300)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    bin_dir = args.bin_dir or str(Path(__file__).resolve().parent.parent.parent / "bin")

    state = PipelineState(
        repo_path=str(Path(args.repo_path).resolve()),
        output_dir=str(Path(args.output_dir).resolve()),
        indexer_bin_dir=bin_dir,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    try:
        t0 = time.time()
        state = run_indexer(state)
        elapsed = time.time() - t0
        print(f"\n✓ KuzuDB at: {state.kuzu_path}")
        print(f"  Elapsed: {elapsed:.1f}s")
    except Exception as e:
        print(f"\n✗ Indexer failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

