"""Stage: Playwright E2E Test Builder.

Reads structured E2E test scenario markdown files (produced by test_agent.py)
and uses an LLM to generate runnable Playwright TypeScript test files.

No KuzuDB / knowledge-graph access is required — the scenario documents already
contain all the context the LLM needs.  One LLM call per flow keeps each call
well within the context window even for large scenario files.

Usage (standalone CLI)::

    python -m codedoc.stages.test_builder \\
        --scenarios-dir ./test-scenarios/inventory-service \\
        --output-dir ./playwright-tests \\
        --provider claude --model claude-sonnet-4-6 --verbose

    # Process a single flow only:
    python -m codedoc.stages.test_builder \\
        --scenarios-dir ./test-scenarios/inventory-service \\
        --output-dir ./playwright-tests \\
        --flow record-inventory-movement \\
        --provider ollama --model qwen3:32b --base-url http://127.0.0.1:11434/v1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from codedoc.llm import create_provider
from codedoc.stages.agent import (
    _WRITE_ARTIFACT_ANTHROPIC,
    _WRITE_ARTIFACT_OPENAI,
    _write_artifact,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "test-builder-prompt.md"
)

_MANIFEST_FILENAME = "manifests/test-scenarios.json"


# ---------------------------------------------------------------------------
# Scenario file reader
# ---------------------------------------------------------------------------

def _read_scenarios(scenarios_dir: str | Path, flow_filter: str | None = None) -> list[dict]:
    """Read scenario markdown files from a test-scenarios directory.

    Looks for ``manifests/test-scenarios.json`` to enumerate files in order.
    Falls back to globbing for ``e2e-scenarios/*.md`` if no manifest exists.

    Args:
        scenarios_dir: Root of the test-scenarios output (contains
            ``e2e-scenarios/`` and ``manifests/``).
        flow_filter: Optional flow name or file-basename filter.  When set,
            only the matching scenario is returned.

    Returns:
        List of dicts with keys ``flow``, ``file``, ``content``.
    """
    root = Path(scenarios_dir).resolve()
    manifest_path = root / _MANIFEST_FILENAME

    # Build file list from manifest or glob
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        file_entries: list[dict] = manifest.get("scenarios", [])
    else:
        file_entries = [
            {"file": str(p.relative_to(root)), "flow": p.stem}
            for p in sorted((root / "e2e-scenarios").glob("*.md"))
        ]

    results: list[dict] = []
    for entry in file_entries:
        rel_file: str = entry.get("file", "")
        flow_name: str = entry.get("flow", Path(rel_file).stem)

        # Skip the overview file — it's not a testable flow
        if Path(rel_file).stem == "00-overview" or flow_name == "overview":
            continue

        # Apply flow filter if given
        if flow_filter:
            slug = _slugify(flow_name)
            basename = Path(rel_file).stem
            if flow_filter not in (flow_name, slug, basename):
                continue

        full_path = root / rel_file
        if not full_path.exists():
            print(
                f"Warning: scenario file not found: {full_path}", file=sys.stderr
            )
            continue

        results.append(
            {
                "flow": flow_name,
                "file": rel_file,
                "content": full_path.read_text(encoding="utf-8"),
                "entry": entry,
            }
        )

    return results


def _slugify(text: str) -> str:
    """Convert a flow name to a filesystem-safe slug."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# System prompt loader
# ---------------------------------------------------------------------------

def _load_system_prompt(
    prompt_path: str | Path,
    repo_name: str,
    output_root: str,
) -> str:
    """Load the test builder system prompt and inject runtime context."""
    path = Path(prompt_path)
    if path.exists():
        prompt = path.read_text(encoding="utf-8")
    else:
        prompt = (
            "You are a Playwright test engineer. "
            "Read the scenario document and generate a runnable .spec.ts file. "
            "Use write_artifact to save it."
        )

    # Strip YAML frontmatter if present
    if prompt.startswith("---"):
        end = prompt.find("---", 3)
        if end != -1:
            prompt = prompt[end + 3:].lstrip()

    # Replace template placeholder and inject runtime context
    prompt = prompt.replace("{{repo_name}}", repo_name)
    prompt += (
        f"\n\n---\n"
        f"## Runtime context\n\n"
        f"- Repository name: `{repo_name}`\n"
        f"- Artifact output root: `{output_root}`\n"
        f"- Write all artifacts using the `write_artifact` tool.\n"
        f"- Use `write_artifact` for the spec file AND for `playwright.config.ts`, "
        f"`package.json`, `fixtures/test-data.ts` after all flows.\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Per-flow LLM loop
# ---------------------------------------------------------------------------

def run_builder_loop(
    provider: Any,
    system_prompt: str,
    scenario_content: str,
    flow_name: str,
    output_root: str,
    max_turns: int = 20,
    verbose: bool = False,
    use_anthropic_format: bool = False,
) -> dict:
    """Run one LLM conversation to generate tests for a single scenario flow.

    The LLM receives the full scenario markdown as the user message and is
    expected to call ``write_artifact`` to emit the ``.spec.ts`` file.

    Returns:
        Dict with ``status``, ``error``, ``artifacts``, ``tool_uses``,
        ``input_tokens``, ``output_tokens``.
    """
    if use_anthropic_format:
        tool_defs = [_WRITE_ARTIFACT_ANTHROPIC]
    else:
        tool_defs = [_WRITE_ARTIFACT_OPENAI]

    user_message = (
        f"Generate a complete Playwright `.spec.ts` test file for the following "
        f"E2E test scenario.\n\n"
        f"Follow the workflow in your system prompt exactly:\n"
        f"1. Parse the scenario sections (3a–3f).\n"
        f"2. Build the TypeScript spec with all HP, EP, EC scenarios.\n"
        f"3. Call `write_artifact` with the spec file.\n\n"
        f"---\n\n"
        f"{scenario_content}"
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    artifacts: list[str] = []
    tool_uses = 0
    total_input = 0
    total_output = 0
    events: list[str] = []

    def log(msg: str) -> None:
        events.append(msg)
        if verbose:
            print(msg, flush=True)

    log(f"[builder] flow='{flow_name}'  max_turns={max_turns}")

    for turn in range(1, max_turns + 1):
        log(f"[builder] LLM call #{turn}")

        try:
            response = provider.chat(messages, tools=tool_defs, tool_choice="auto")
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"LLM API error: {exc}",
                "artifacts": artifacts,
                "tool_uses": tool_uses,
                "input_tokens": total_input,
                "output_tokens": total_output,
            }

        total_input += response.input_tokens
        total_output += response.output_tokens

        # No tool calls → done
        if not response.tool_calls:
            log("[builder] model finished (stop)")
            if response.content:
                messages.append({"role": "assistant", "content": response.content})
            break

        if response.stop_reason == "max_tokens":
            log("[builder] WARNING: max_tokens reached")
            break

        # Append assistant turn
        if use_anthropic_format:
            tc_list = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]
        else:
            tc_list = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]
        messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": tc_list,
            }
        )

        # Dispatch tool calls
        for tc in response.tool_calls:
            tool_uses += 1
            args_summary = json.dumps(tc.arguments)
            if len(args_summary) > 120:
                args_summary = args_summary[:120] + "…"
            log(f"[tool] {tc.name}({args_summary})")

            if tc.name == "write_artifact":
                result_text = _write_artifact(
                    output_root,
                    tc.arguments.get("filename", "unknown.ts"),
                    tc.arguments.get("content", ""),
                )
                if result_text.startswith("written:"):
                    artifacts.append(result_text[len("written:"):].strip())
            else:
                result_text = f"Unknown tool: {tc.name}"

            log(f"[tool] → {result_text[:200]}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
            )
    else:
        return {
            "status": "failed",
            "error": f"exceeded {max_turns} turns without finishing",
            "artifacts": artifacts,
            "tool_uses": tool_uses,
            "input_tokens": total_input,
            "output_tokens": total_output,
        }

    return {
        "status": "done",
        "error": None,
        "artifacts": artifacts,
        "tool_uses": tool_uses,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


# ---------------------------------------------------------------------------
# Shared-files generator (playwright.config.ts, package.json, fixtures)
# ---------------------------------------------------------------------------

def run_shared_files_loop(
    provider: Any,
    system_prompt: str,
    repo_name: str,
    generated_flows: list[str],
    output_root: str,
    max_turns: int = 10,
    verbose: bool = False,
    use_anthropic_format: bool = False,
) -> dict:
    """Ask the LLM to generate playwright.config.ts, package.json, fixtures/test-data.ts."""
    if use_anthropic_format:
        tool_defs = [_WRITE_ARTIFACT_ANTHROPIC]
    else:
        tool_defs = [_WRITE_ARTIFACT_OPENAI]

    flows_list = "\n".join(f"  - {f}" for f in generated_flows)
    user_message = (
        f"All scenario spec files have been generated. "
        f"Now write the three shared project files:\n\n"
        f"1. `playwright.config.ts` — use the template from your system prompt, "
        f"   baseURL defaults to `http://localhost:8080`.\n"
        f"2. `package.json` — use `{repo_name}-e2e` as the name.\n"
        f"3. `fixtures/test-data.ts` — export a single object `TEST_DATA` with "
        f"   a key per flow slug containing an empty object placeholder "
        f"   (the real payloads live inside each spec file).\n\n"
        f"Flows generated:\n{flows_list}\n\n"
        f"Call `write_artifact` once for each file."
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    artifacts: list[str] = []
    tool_uses = 0
    total_input = 0
    total_output = 0
    events: list[str] = []

    def log(msg: str) -> None:
        events.append(msg)
        if verbose:
            print(msg, flush=True)

    log("[builder] generating shared project files")

    for turn in range(1, max_turns + 1):
        log(f"[builder] LLM call #{turn}")

        try:
            response = provider.chat(messages, tools=tool_defs, tool_choice="auto")
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"LLM API error: {exc}",
                "artifacts": artifacts,
                "tool_uses": tool_uses,
                "input_tokens": total_input,
                "output_tokens": total_output,
            }

        total_input += response.input_tokens
        total_output += response.output_tokens

        if not response.tool_calls:
            log("[builder] shared files: model finished")
            break

        if response.stop_reason == "max_tokens":
            log("[builder] WARNING: max_tokens reached on shared files")
            break

        if use_anthropic_format:
            tc_list = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]
        else:
            tc_list = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]
        messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": tc_list,
            }
        )

        for tc in response.tool_calls:
            tool_uses += 1
            args_summary = json.dumps(tc.arguments)
            if len(args_summary) > 120:
                args_summary = args_summary[:120] + "…"
            log(f"[tool] {tc.name}({args_summary})")

            if tc.name == "write_artifact":
                result_text = _write_artifact(
                    output_root,
                    tc.arguments.get("filename", "unknown"),
                    tc.arguments.get("content", ""),
                )
                if result_text.startswith("written:"):
                    artifacts.append(result_text[len("written:"):].strip())
            else:
                result_text = f"Unknown tool: {tc.name}"

            log(f"[tool] → {result_text[:200]}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
            )
    else:
        return {
            "status": "failed",
            "error": f"exceeded {max_turns} turns on shared files",
            "artifacts": artifacts,
            "tool_uses": tool_uses,
            "input_tokens": total_input,
            "output_tokens": total_output,
        }

    return {
        "status": "done",
        "error": None,
        "artifacts": artifacts,
        "tool_uses": tool_uses,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the Playwright test builder standalone."""
    parser = argparse.ArgumentParser(
        description=(
            "Playwright E2E test builder — reads test scenario markdown files and "
            "generates runnable Playwright TypeScript test suites."
        )
    )
    parser.add_argument(
        "--scenarios-dir",
        required=True,
        help="Root of the test-scenarios output directory "
             "(must contain e2e-scenarios/ and manifests/).",
    )
    parser.add_argument(
        "--output-dir",
        default="./playwright-tests",
        help="Root directory for generated Playwright files (default: ./playwright-tests).",
    )
    parser.add_argument(
        "--flow",
        default="",
        help="Process only this flow (flow name, slug, or file basename). "
             "Omit to process all flows.",
    )
    parser.add_argument(
        "--repo-name",
        default="",
        help="Repository name for package.json (inferred from --scenarios-dir if omitted).",
    )
    parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "claude", "ollama", "openai"],
        help="LLM provider (default: auto).",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Model name (default: claude-sonnet-4-6).",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="API base URL (for Ollama/OpenAI-compatible endpoints).",
    )
    parser.add_argument(
        "--prompt",
        default="",
        help="Path to a custom system prompt .md file.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=20,
        help="Maximum LLM turns per flow (default: 20).",
    )
    parser.add_argument(
        "--skip-shared",
        action="store_true",
        help="Skip generating playwright.config.ts / package.json / fixtures.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print tool call details to stdout.",
    )
    args = parser.parse_args()

    scenarios_dir = Path(args.scenarios_dir).resolve()
    if not scenarios_dir.exists():
        print(
            f"Error: scenarios directory not found: {scenarios_dir}", file=sys.stderr
        )
        sys.exit(1)

    # Infer repo name from manifest or directory structure
    repo_name = args.repo_name
    if not repo_name:
        manifest_path = scenarios_dir / _MANIFEST_FILENAME
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            repo_name = manifest.get("repo_name", "")
        if not repo_name:
            repo_name = scenarios_dir.name

    # Read scenario files
    flow_filter = args.flow.strip() or None
    scenarios = _read_scenarios(scenarios_dir, flow_filter=flow_filter)

    if not scenarios:
        if flow_filter:
            print(
                f"Error: no scenario found matching flow '{flow_filter}' "
                f"in {scenarios_dir}",
                file=sys.stderr,
            )
        else:
            print(
                f"Error: no scenario files found in {scenarios_dir}", file=sys.stderr
            )
        sys.exit(1)

    print(
        f"Found {len(scenarios)} scenario(s) to process in '{scenarios_dir}'."
    )

    # Detect effective provider for auth check
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

    if effective_provider in ("claude", "anthropic") and not os.environ.get(
        "ANTHROPIC_API_KEY"
    ):
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
    except Exception as exc:
        print(f"Error initializing LLM provider: {exc}", file=sys.stderr)
        sys.exit(1)

    use_anthropic = effective_provider in ("claude", "anthropic")

    output_root = str(Path(args.output_dir).resolve())
    Path(output_root).mkdir(parents=True, exist_ok=True)

    prompt_path = args.prompt or str(_PROMPT_PATH)
    system_prompt = _load_system_prompt(prompt_path, repo_name, output_root)

    # ── Process each scenario flow ─────────────────────────────────────────
    t0 = time.time()
    total_input = 0
    total_output = 0
    total_tool_uses = 0
    all_artifacts: list[str] = []
    generated_flows: list[str] = []
    failed_flows: list[str] = []

    for scenario in scenarios:
        flow_name = scenario["flow"]
        print(f"\n→ Processing flow: '{flow_name}' ...")

        result = run_builder_loop(
            provider=provider,
            system_prompt=system_prompt,
            scenario_content=scenario["content"],
            flow_name=flow_name,
            output_root=output_root,
            max_turns=args.max_turns,
            verbose=args.verbose,
            use_anthropic_format=use_anthropic,
        )

        total_input += result["input_tokens"]
        total_output += result["output_tokens"]
        total_tool_uses += result["tool_uses"]
        all_artifacts.extend(result["artifacts"])

        if result["status"] == "done":
            generated_flows.append(flow_name)
            print(
                f"  ✓ {flow_name}  "
                f"({result['tool_uses']} tool call(s), "
                f"{result['input_tokens']:,}+{result['output_tokens']:,} tokens)"
            )
        else:
            failed_flows.append(flow_name)
            print(
                f"  ✗ {flow_name}: {result['error']}", file=sys.stderr
            )

    # ── Shared project files ───────────────────────────────────────────────
    if generated_flows and not args.skip_shared:
        print("\n→ Generating shared project files ...")
        shared_result = run_shared_files_loop(
            provider=provider,
            system_prompt=system_prompt,
            repo_name=repo_name,
            generated_flows=generated_flows,
            output_root=output_root,
            max_turns=args.max_turns,
            verbose=args.verbose,
            use_anthropic_format=use_anthropic,
        )
        total_input += shared_result["input_tokens"]
        total_output += shared_result["output_tokens"]
        total_tool_uses += shared_result["tool_uses"]
        all_artifacts.extend(shared_result["artifacts"])

        if shared_result["status"] == "done":
            print(
                f"  ✓ shared files  "
                f"({shared_result['tool_uses']} tool call(s))"
            )
        else:
            print(
                f"  ✗ shared files: {shared_result['error']}", file=sys.stderr
            )

    elapsed = time.time() - t0

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\nElapsed:    {elapsed:.1f}s")
    print(f"Flows OK:   {len(generated_flows)} / {len(scenarios)}")
    print(f"Tool calls: {total_tool_uses}")
    print(
        f"Tokens:     {total_input:,} input / "
        f"{total_output:,} output / "
        f"{total_input + total_output:,} total"
    )

    if all_artifacts:
        print(f"\nArtifacts written ({len(all_artifacts)}):")
        for a in all_artifacts:
            print(f"  {a}")
    else:
        print("\nNo artifacts written.")

    if failed_flows:
        print(f"\nFailed flows ({len(failed_flows)}):", file=sys.stderr)
        for f in failed_flows:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
