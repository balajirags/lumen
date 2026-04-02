"""Stage: E2E Test Scenario Agent.

Uses the knowledge graph (KuzuDB) and source file reading to trace user flows
and produce E2E test scenario documentation artifacts (markdown).

The LLM provider is **injected** — never hardcoded.  Supports Claude, Ollama,
OpenAI, and any OpenAI-compatible endpoint.

Usage (standalone CLI)::

    python -m codedoc.stages.test_agent \\
        --db-path ./index.kuzu/my-project-db \\
        --repo-path /path/to/source/repo \\
        --output-dir ./test-scenarios \\
        --provider claude --model claude-sonnet-4-6 --verbose

    python -m codedoc.stages.test_agent \\
        --db-path ./index.kuzu/my-project-db \\
        --repo-path /path/to/source/repo \\
        --provider ollama --model qwen3.5:35b --base-url http://127.0.0.1:11434/v1
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from codedoc.kg_tools import KuzuBackend, ReverseEngineerToolkit
from codedoc.llm import create_provider
from codedoc.prompts import GRAPH_CONVENTIONS_BASE
from codedoc.stages.agent import (
    _WRITE_ARTIFACT_ANTHROPIC,
    _WRITE_ARTIFACT_OPENAI,
    _write_artifact,
    run_loop,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "test-agent-prompt.md"

_MAX_SOURCE_FILE_BYTES = 50_000  # 50 KB cap per file read

# Extend base graph conventions with test-agent-specific efficiency hint
_GRAPH_CONVENTIONS = GRAPH_CONVENTIONS_BASE.replace(
    "- **Use `search_nodes` before `query`** for name lookups.",
    "- **Use `trace_user_flow` first** for any flow — then `read_source_file` on key files.\n"
    "- **Use `search_nodes` before `query`** for name lookups.",
)


# ---------------------------------------------------------------------------
# read_source_file tool (test-agent-specific)
# ---------------------------------------------------------------------------

def _read_source_file(repo_root: str, file_path: str) -> str:
    """Read a source file from the repository and return its contents.

    Resolves ``file_path`` against ``repo_root``. Handles both absolute paths
    (as stored in the graph) and relative paths. Enforces a 50 KB size cap and
    rejects path traversal attempts.
    """
    if not file_path or not file_path.strip():
        return "Error: file_path is required."

    repo = Path(repo_root).resolve()

    # Try the path as-is first (may be absolute from the graph)
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = repo / file_path

    # Resolve and validate — must stay within repo root
    try:
        resolved = candidate.resolve()
        resolved.relative_to(repo)  # raises ValueError if outside
    except ValueError:
        return f"Error: path '{file_path}' is outside the repository root — access denied."
    except Exception as e:
        return f"Error resolving path '{file_path}': {e}"

    if not resolved.exists():
        # Try stripping a leading component (some graphs store paths as
        # "src/main/java/..." relative to a sub-directory)
        stripped = repo / Path(file_path).name
        if stripped.exists():
            resolved = stripped
        else:
            return f"File not found: {file_path}"

    if not resolved.is_file():
        return f"Not a file: {file_path}"

    size = resolved.stat().st_size
    if size > _MAX_SOURCE_FILE_BYTES:
        return (
            f"File too large ({size:,} bytes > {_MAX_SOURCE_FILE_BYTES:,} byte limit): {file_path}\n"
            f"Read a specific line range using the `query` tool with a MATCH on Statement nodes instead."
        )

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error reading file '{file_path}': {e}"

    return f"// {resolved}\n{content}"


def _make_read_source_file_fn(repo_root: str):
    """Return a closure that binds repo_root for use as a registered tool."""
    def read_source_file(file_path: str) -> str:
        """Read a source file from the repository by its path.

        Use the file path from the graph (File.path property or Source_FILE
        relationship target). The file must be within the repository root.
        File size is capped at 50 KB.

        Args:
            file_path: Relative or absolute path to the source file.
        """
        return _read_source_file(repo_root, file_path)
    return read_source_file


_READ_SOURCE_FILE_OPENAI = {
    "type": "function",
    "function": {
        "name": "read_source_file",
        "description": (
            "Read a source file from the repository and return its full contents. "
            "Use the file path stored in the graph (File.path or from SOURCE_FILE relationship). "
            "File size is capped at 50 KB."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Relative or absolute path to the source file as stored in the graph, "
                        "e.g. 'src/main/java/com/example/InventoryController.java'."
                    ),
                },
            },
            "required": ["file_path"],
        },
    },
}

_READ_SOURCE_FILE_ANTHROPIC = {
    "name": "read_source_file",
    "description": (
        "Read a source file from the repository and return its full contents. "
        "Use the file path stored in the graph (File.path or from SOURCE_FILE relationship). "
        "File size is capped at 50 KB."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Relative or absolute path to the source file as stored in the graph, "
                    "e.g. 'src/main/java/com/example/InventoryController.java'."
                ),
            },
        },
        "required": ["file_path"],
    },
}


# ---------------------------------------------------------------------------
# System prompt loader
# ---------------------------------------------------------------------------

def _load_system_prompt(
    prompt_path: str | Path,
    db_path: str,
    repo_path: str,
    repo_name: str,
    output_root: str,
) -> str:
    """Load the test agent system prompt and inject runtime context."""
    path = Path(prompt_path)
    if path.exists():
        prompt = path.read_text(encoding="utf-8")
    else:
        prompt = (
            "You are an E2E test scenario agent. "
            "Use the provided tools to trace user flows and write test scenario artifacts."
        )

    # Strip YAML frontmatter if present
    if prompt.startswith("---"):
        end = prompt.find("---", 3)
        if end != -1:
            prompt = prompt[end + 3:].lstrip()

    prompt += _GRAPH_CONVENTIONS
    prompt += (
        f"\n\n---\n"
        f"## Runtime context\n\n"
        f"- KuzuDB path: `{db_path}`\n"
        f"- Repository source path: `{repo_path}`\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Artifact output root: `{output_root}`\n"
        f"- Write all artifacts using the `write_artifact` tool.\n"
        f"- Read source files using the `read_source_file` tool "
        f"  (paths come from the graph's File.path property).\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Test agent loop (delegates to run_loop with extra read_source_file tool)
# ---------------------------------------------------------------------------

def run_test_loop(
    provider,
    toolkit: ReverseEngineerToolkit,
    repo_root: str,
    system_prompt: str,
    user_request: str,
    output_root: str,
    max_turns: int = 60,
    verbose: bool = False,
    use_anthropic_format: bool = False,
    max_context_tokens: int = 120_000,
) -> dict:
    """Run the test agent agentic loop.

    Delegates to ``run_loop`` from agent.py, extending it with
    ``read_source_file`` dispatching via the ``extra_tools`` parameter.
    All KG tools + write_artifact + read_source_file are available to the LLM.

    Returns:
        Dict with status, error, artifacts, events, tool_uses,
        input_tokens, output_tokens.
    """
    read_source_file_fn = _make_read_source_file_fn(repo_root)

    if use_anthropic_format:
        extra_defs = [_READ_SOURCE_FILE_ANTHROPIC]
    else:
        extra_defs = [_READ_SOURCE_FILE_OPENAI]

    return run_loop(
        provider=provider,
        toolkit=toolkit,
        system_prompt=system_prompt,
        user_request=user_request,
        output_root=output_root,
        max_turns=max_turns,
        verbose=verbose,
        use_anthropic_format=use_anthropic_format,
        extra_tools={"read_source_file": read_source_file_fn},
        extra_tool_defs=extra_defs,
        max_context_tokens=max_context_tokens,
    )


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the E2E test scenario agent standalone."""
    parser = argparse.ArgumentParser(
        description=(
            "E2E test scenario agent — trace user flows via KuzuDB and "
            "generate test scenario documentation."
        )
    )
    parser.add_argument("--db-path", required=True,
                        help="Path to KuzuDB database file or directory")
    parser.add_argument("--repo-path", required=True,
                        help="Path to the original source repository (for reading source files)")
    parser.add_argument("--output-dir", default="./test-scenarios",
                        help="Root directory for test scenario artifacts (default: ./test-scenarios)")
    parser.add_argument("--repo-name", default="",
                        help="Repository name label (inferred from --repo-path if omitted)")
    parser.add_argument("--request", default="",
                        help="Custom user request text (optional)")
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "claude", "ollama", "openai"],
                        help="LLM provider (default: auto)")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Model name (default: claude-sonnet-4-6)")
    parser.add_argument("--base-url", default="",
                        help="API base URL (for Ollama/OpenAI-compatible endpoints)")
    parser.add_argument("--prompt", default="",
                        help="Path to a custom system prompt .md file")
    parser.add_argument("--max-turns", type=int, default=60,
                        help="Maximum LLM turns (default: 60)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print tool call details to stdout")
    args = parser.parse_args()

    # Resolve paths
    repo_path = str(Path(args.repo_path).resolve())
    repo_name = args.repo_name or Path(repo_path).name

    db_path = args.db_path
    # If a directory was given, find the first file inside (KuzuDB stores data as a single file)
    db_candidate = Path(db_path)
    if db_candidate.is_dir():
        db_files = [f for f in db_candidate.iterdir() if f.is_file()]
        if not db_files:
            print(f"Error: no KuzuDB file found in directory: {db_path}", file=sys.stderr)
            sys.exit(1)
        db_path = str(db_files[0])

    try:
        backend = KuzuBackend(db_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    toolkit = ReverseEngineerToolkit(backend)

    # Detect effective provider before creating it so we can give useful errors
    effective_provider = args.provider.lower()
    if effective_provider == "auto":
        if args.model.startswith("claude") or args.model.startswith("anthropic"):
            effective_provider = "claude"
        elif "ollama" in args.base_url or "11434" in args.base_url:
            effective_provider = "ollama"
        elif args.base_url:
            effective_provider = "openai"
        else:
            effective_provider = "ollama" if ":" in args.model else "claude"

    if effective_provider in ("claude", "anthropic") and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "Error: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Set it with:  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "Or use a different provider:  --provider ollama --base-url http://127.0.0.1:11434/v1",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        provider = create_provider(
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
        )
    except Exception as e:
        print(f"Error initializing LLM provider: {e}", file=sys.stderr)
        sys.exit(1)

    use_anthropic = effective_provider in ("claude", "anthropic")

    artifacts_dir = str(Path(args.output_dir) / repo_name)
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    prompt_path = args.prompt or str(_PROMPT_PATH)
    system_prompt = _load_system_prompt(
        prompt_path, db_path, repo_path, repo_name, artifacts_dir
    )

    user_request = args.request or (
        "Perform a full E2E test scenario analysis of this codebase. "
        "Discover all major user-facing flows from entry points. "
        "Trace each flow through the system using the graph and source files. "
        "Write comprehensive test scenario artifacts covering happy paths, error paths, and edge cases."
    )

    t0 = time.time()
    result = run_test_loop(
        provider=provider,
        toolkit=toolkit,
        repo_root=repo_path,
        system_prompt=system_prompt,
        user_request=user_request,
        output_root=artifacts_dir,
        max_turns=args.max_turns,
        verbose=args.verbose,
        use_anthropic_format=use_anthropic,
    )
    elapsed = time.time() - t0

    # ── Summary output ──────────────────────────────────────────────────────
    print(f"\nElapsed:    {elapsed:.1f}s")
    print(f"Status:     {result['status']}")
    print(f"Tool calls: {result['tool_uses']}")
    print(
        f"Tokens:     {result['input_tokens']:,} input / "
        f"{result['output_tokens']:,} output / "
        f"{result['input_tokens'] + result['output_tokens']:,} total"
    )

    if result["artifacts"]:
        print(f"\nArtifacts written ({len(result['artifacts'])}):")
        for a in result["artifacts"]:
            print(f"  {a}")
    else:
        print("\nNo artifacts written.")

    if result["error"]:
        print(f"\nError: {result['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
