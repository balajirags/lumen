"""Stage 1 — Indexer node.

Wraps the existing cmg-* binaries to produce a KuzuDB graph from a repo.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from codedoc import log as _log
from codedoc.artifact_planner import build_artifact_plan
from codedoc.language_registry import LANGUAGE_CATEGORY_BY_KEY
from codedoc.repo_classification import classify_repo
from codedoc.state import PipelineState


def treesitter_languages(ordered_languages: list[str], language_flavors: list[str] | None) -> list[str]:
    """Map the repo's detected language categories to the tree-sitter engine's
    language keys (``treesitter-indexer/languages.py``), preserving order.

    The pipeline's ``"jvm"`` category covers both Java and Kotlin; the two are
    told apart by ``language_flavors``. A JVM slice with neither flavor
    recorded is treated as Java. A mixed Java+Kotlin slice yields both keys:
    the engine merges languages in memory, so they no longer need to be
    exclusive.
    """
    flavors = language_flavors or []
    keys: list[str] = []
    for category in ordered_languages:
        if category == "jvm":
            if "java" in flavors or "kotlin" not in flavors:
                keys.append("java")
            if "kotlin" in flavors:
                keys.append("kotlin")
        else:
            keys.append(category)  # "python", "php", "js" match the engine's keys
    return keys


def detect_languages(repo_path: str) -> dict[str, int]:
    """Return all supported language categories found in the repo with their file counts."""
    metrics = classify_repo(repo_path)
    detected = dict(metrics.get("files_by_category", {}))
    if not detected:
        raise ValueError(
            f"No supported source files found in {repo_path}. "
            "Expected Java/Kotlin, JavaScript/TypeScript, Python, or PHP files."
        )
    return detected

def _stderr_excerpt(stderr: str) -> str:
    lines = stderr.strip().splitlines() if stderr else []
    if not lines:
        return "(no stderr)"
    if len(lines) <= 20:
        return "\n".join(lines)
    head = "\n".join(lines[:10])
    tail = "\n".join(lines[-10:])
    return f"{head}\n[... {len(lines) - 20} lines omitted ...]\n{tail}"

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
    classification = classify_repo(str(repo))
    detected = dict(classification.get("files_by_category", {}))
    if not detected:
        raise ValueError(
            f"No supported source files found in {repo}. "
            "Expected Java/Kotlin, JavaScript/TypeScript, Python, or PHP files."
        )
    ordered_languages = [lang for lang, _count in sorted(detected.items(), key=lambda item: (-item[1], item[0]))]
    state.language_categories = ordered_languages
    state.language_flavors = list(classification.get("language_flavors", []))
    state.archetype_signals = list(classification.get("archetype_signals", []))
    state.selected_archetype = str(classification.get("selected_archetype", ""))
    state.primary_repo_type = str(classification.get("primary_repo_type", state.selected_archetype))
    state.capabilities = list(classification.get("capabilities", []))
    state.artifact_plan = build_artifact_plan(state.primary_repo_type or state.selected_archetype, classification)
    state.indexed_languages = ordered_languages
    state.log(
        stage,
        "Detected language categories: " + ", ".join(
            f"{LANGUAGE_CATEGORY_BY_KEY[lang].display_name} ({detected[lang]:,} files)" for lang in ordered_languages
        ),
    )
    if state.primary_repo_type:
        state.log(stage, f"Selected primary repo type: {state.primary_repo_type}")
    if state.capabilities:
        state.log(stage, f"Detected capabilities: {', '.join(state.capabilities)}")

    # index.kuzu/ is the container directory passed as --db-path.
    # All binaries create {repo_name}-db inside it.
    effective_repo_name = state.repo_name or repo.name
    kuzu_dir = Path(state.output_dir).resolve() / "index.kuzu"
    kuzu_dir.mkdir(parents=True, exist_ok=True)

    use_treesitter = state.experimental_treesitter
    if use_treesitter:
        state.graph_backend = "ladybug"
        state.log(stage, "Using experimental tree-sitter indexer (cmg-ts)")

    # One job per binary invocation: the tree-sitter engine indexes every
    # detected language in a single cmg-ts run (it merges them in memory), the
    # legacy engine runs one cmg-* binary per language category.
    jobs: list[tuple[list[str], str, Path, list[str]]] = []  # (displays, binary_name, binary, extra_args)
    if use_treesitter:
        ts_languages = treesitter_languages(ordered_languages, state.language_flavors)
        displays = [LANGUAGE_CATEGORY_BY_KEY[lang].display_name for lang in ordered_languages]
        jobs.append((displays, "cmg-ts", Path(state.treesitter_indexer_bin_dir) / "cmg-ts",
                     ["--languages", ",".join(ts_languages)]))
    else:
        for language in ordered_languages:
            definition = LANGUAGE_CATEGORY_BY_KEY[language]
            jobs.append(([definition.display_name], definition.binary_name,
                         Path(state.indexer_bin_dir) / definition.binary_name,
                         ["--language", definition.cli_language] if definition.cli_language else []))

    _log.start_indexer_progress([LANGUAGE_CATEGORY_BY_KEY[lang].display_name for lang in ordered_languages])
    try:
        for displays, binary_name, binary, extra_args in jobs:
            display = ", ".join(displays)
            if not binary.exists():
                hint = (
                    "Run treesitter-indexer/install.sh or check your treesitter_indexer_bin_dir setting."
                    if use_treesitter
                    else "Run the install script or check your indexer_bin_dir setting."
                )
                raise FileNotFoundError(f"Indexer binary not found: {binary}\n{hint}")

            for name in displays:
                _log.update_indexer_progress(name, "running")
            state.log(stage, f"Running {binary_name} for {display} → {kuzu_dir}/{effective_repo_name}-db")
            cmd = [str(binary), str(repo), _DB_FLAG, str(kuzu_dir), "--repo-name", effective_repo_name]
            cmd.extend(extra_args)
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=state.timeout,
                )
            except subprocess.TimeoutExpired:
                for name in displays:
                    _log.update_indexer_progress(name, "failed")
                raise TimeoutError(
                    f"{binary_name} timed out after {state.timeout}s. "
                    "Try increasing --timeout or indexing a smaller repo."
                )

            if state.verbose:
                if proc.stdout:
                    state.log(stage, f"{binary_name} stdout:\n{proc.stdout}")
                if proc.stderr:
                    state.log(stage, f"{binary_name} stderr:\n{proc.stderr}")

            if proc.returncode != 0:
                for name in displays:
                    _log.update_indexer_progress(name, "failed")
                hint = (
                    "\nProcess was killed (SIGKILL) — likely out of memory. "
                    "Increase Docker Desktop memory to at least 8 GB (Settings → Resources → Memory)."
                    if proc.returncode == -9 else ""
                )
                raise RuntimeError(
                    f"{binary_name} exited with code {proc.returncode}.{hint}\n{_stderr_excerpt(proc.stderr)}"
                )

            for name in displays:
                _log.update_indexer_progress(name, "done")
    finally:
        _log.stop_indexer_progress()

    # --- Locate the database created by the indexer ---
    # All binaries (Java, JS, Python) create {repo_name}-db inside --db-path.
    # With KuzuDB 0.11.x this is a single FILE; older versions produce a directory.
    expected = kuzu_dir / f"{effective_repo_name}-db"
    if expected.exists():
        kuzu_path = str(expected)
        kuzu = expected
    else:
        # Fallback: scan kuzu_dir for a single non-hidden entry (handles name mismatches)
        entries = [e for e in kuzu_dir.iterdir() if not e.name.startswith(".")]
        if len(entries) == 1:
            kuzu_path = str(entries[0])
            kuzu = entries[0]
            state.log(stage, f"KuzuDB entry name '{entries[0].name}' differs from expected '{effective_repo_name}-db'")
        elif len(entries) == 0:
            state.log(stage, f"stdout:\n{proc.stdout[-1000:]}" if proc.stdout else "No stdout")
            raise FileNotFoundError(
                f"Indexer completed but no KuzuDB output found in {kuzu_dir}. "
                "Check the repo contents and indexer output."
            )
        else:
            state.log(stage, f"stdout:\n{proc.stdout[-1000:]}" if proc.stdout else "No stdout")
            raise FileNotFoundError(
                f"Indexer created {len(entries)} entries in {kuzu_dir} but none match "
                f"expected '{effective_repo_name}-db'. Found: {[e.name for e in entries]}"
            )

    total_size = sum(f.stat().st_size for f in (kuzu.rglob("*") if kuzu.is_dir() else [kuzu]) if f.is_file())
    if total_size < 1024:
        raise ValueError(
            f"KuzuDB output at {kuzu_path} is only {total_size} bytes — "
            "too small to be a valid graph. Check the repo contents."
        )

    state.kuzu_path = kuzu_path
    state.log(
        stage,
        f"Indexing complete. KuzuDB at {kuzu_path} ({total_size:,} bytes) "
        f"for {len(state.language_categories or state.indexed_languages)} language slice(s).",
    )
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
    parser.add_argument("--output-dir", default="./output",
                        help="Output directory (default: ./output)")
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
