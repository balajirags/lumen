"""codedoc CLI — entry point for the code intelligence pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from codedoc.config import load_config
from codedoc.pipeline import run_pipeline


@click.group()
def main() -> None:
    """Code Intelligence Pipeline — generate docs from source code."""


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--output-dir", type=click.Path(), default=None, help="Output directory (default: ./codedoc-output)")
@click.option("--repo-name", default=None, help="Override repository name used in output directory (useful when repo is mounted at a generic path like /repo)")
@click.option("--model", default=None, help="Model name (default: claude-sonnet-4-6). For Ollama use tool-capable models: llama3.1, qwen2.5, mistral.")
@click.option("--provider", default=None, type=click.Choice(["auto", "anthropic", "ollama", "openai"]), help="LLM provider (default: auto-detect from model name)")
@click.option("--base-url", default=None, help="Custom API base URL (e.g. http://localhost:11434/v1 for Ollama)")
@click.option("--max-turns", type=int, default=None, help="Max agent tool turns (default: 40)")
@click.option("--timeout", type=int, default=None, help="Per-stage timeout in seconds (default: 300)")
@click.option("--verbose", is_flag=True, default=False, help="Print full subprocess output")
def run(
    repo_path: str,
    output_dir: str | None,
    repo_name: str | None,
    model: str | None,
    provider: str | None,
    base_url: str | None,
    max_turns: int | None,
    timeout: int | None,
    verbose: bool,
) -> None:
    """Run the full pipeline: index → agent → build.

    REPO_PATH is the local path to the source code repository.
    """
    cfg = load_config({
        "output_dir": output_dir,
        "model": model,
        "provider": provider,
        "base_url": base_url,
        "max_turns": max_turns,
        "timeout": timeout,
        "verbose": verbose or None,
    })

    state = run_pipeline(
        repo_path=repo_path,
        repo_name=repo_name,
        output_dir=cfg.output_dir,
        model=cfg.model,
        provider=cfg.provider,
        base_url=cfg.base_url,
        max_turns=cfg.max_turns,
        max_context_tokens=cfg.max_context_tokens,
        timeout=cfg.timeout,
        verbose=cfg.verbose,
        indexer_bin_dir=cfg.indexer_bin_dir,
        agent_prompt=cfg.agent_prompt,
        build_script=cfg.build_script,
    )

    if state.status == "done":
        click.echo(f"\n✓ Done in {state.output_dir}")
        if state.site_path:
            click.echo(f"  Doc-site : {state.site_path}")
        if state.artifacts_dir:
            artifacts = list(Path(state.artifacts_dir).rglob("*.md"))
            click.echo(f"  Artifacts: {len(artifacts)} file(s) in {state.artifacts_dir}")
        click.echo(f"  Details  : {state.output_dir}/pipeline.json")
        sys.exit(0)
    else:
        click.echo(f"\n✗ Pipeline did not complete (status: {state.status})", err=True)
        if state.error:
            click.echo(f"  Error: {state.error}", err=True)
        if state.output_dir:
            click.echo(f"  Details: {state.output_dir}/pipeline.json", err=True)
        if state.artifacts_dir and Path(state.artifacts_dir).exists():
            artifacts = list(Path(state.artifacts_dir).rglob("*.md"))
            if artifacts:
                click.echo(f"  Partial artifacts ({len(artifacts)} file(s)):", err=True)
                for a in artifacts:
                    click.echo(f"    {a.relative_to(state.artifacts_dir)}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
