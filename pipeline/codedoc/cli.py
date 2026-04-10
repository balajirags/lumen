"""codedoc CLI — entry point for the code intelligence pipeline."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click

from codedoc.config import load_config
from codedoc.mcp_server import (
    format_client_snippets,
    format_http_client_snippets,
    format_mcp_command,
    format_mcp_http_command,
    format_mcp_http_docker_command,
    format_mcp_http_url,
    serve_mcp,
)
from codedoc.pipelines.full import run_pipeline
from codedoc.pipelines.mcp_http import run_mcp_http_pipeline
from codedoc.pipelines.mcp import run_mcp_pipeline


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
@click.option("--max-turns", type=int, default=None, help="Max agent tool turns (default: 60)")
@click.option("--timeout", type=int, default=None, help="Per-stage timeout in seconds (default: 300)")
@click.option("--repo-size-check", default=None, type=click.Choice(["off", "warn", "strict"]), help="Repo size guardrail mode (default: warn)")
@click.option("--allow-xlarge", is_flag=True, default=False, help="Allow the full docs pipeline to continue even when preflight classifies the repo as xlarge")
@click.option("--ollama-num-ctx", type=int, default=None, help="Ollama KV-cache context window size (default: 131072). Ignored for Claude/OpenAI. Increase for xlarge repos if RAM allows.")
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
    repo_size_check: str | None,
    allow_xlarge: bool,
    ollama_num_ctx: int | None,
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
        "repo_size_check": repo_size_check,
        "allow_xlarge": allow_xlarge or None,
        "ollama_num_ctx": ollama_num_ctx,
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
        ollama_num_ctx=cfg.ollama_num_ctx,
        timeout=cfg.timeout,
        repo_size_check=cfg.repo_size_check,
        allow_xlarge=cfg.allow_xlarge,
        timeout_explicit=cfg.timeout_explicit,
        max_turns_explicit=cfg.max_turns_explicit,
        verbose=cfg.verbose,
        indexer_bin_dir=cfg.indexer_bin_dir,
        agent_prompt=cfg.agent_prompt,
        build_script=cfg.build_script,
    )

    from codedoc import log as _log

    def _elapsed() -> float:
        try:
            return (
                datetime.fromisoformat(state.finished_at) - datetime.fromisoformat(state.started_at)
            ).total_seconds()
        except Exception:
            return 0.0

    if state.status == "done":
        artifacts = list(Path(state.artifacts_dir).rglob("*.md")) if state.artifacts_dir else []
        _log.print_success_panel(
            output_dir=state.output_dir,
            site_path=state.site_path or None,
            artifacts_dir=state.artifacts_dir or None,
            artifact_count=len(artifacts),
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            tool_uses=state.tool_uses,
            elapsed=_elapsed(),
        )
        sys.exit(0)
    else:
        artifacts = (
            list(Path(state.artifacts_dir).rglob("*.md"))
            if state.artifacts_dir and Path(state.artifacts_dir).exists()
            else []
        )
        partial = [str(a.relative_to(state.artifacts_dir)) for a in artifacts] if artifacts else []
        _log.print_failure_panel(
            status=state.status,
            error=state.error,
            output_dir=state.output_dir or None,
            partial_artifacts=partial,
        )
        sys.exit(1)


@main.command()
@click.argument("repo_path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option("--db-path", default=None, type=click.Path(exists=True), help="Serve an existing Kuzu DB directly")
@click.option("--repo-path", "served_repo_path", default=None, type=click.Path(exists=True, file_okay=False), help="Optional source repo path when serving an existing DB")
@click.option("--output-dir", type=click.Path(), default=None, help="Output directory (default: ./codedoc-output)")
@click.option("--repo-name", default=None, help="Override repository name used in output directory")
@click.option("--timeout", type=int, default=None, help="Per-stage timeout in seconds (default: 300)")
@click.option("--repo-size-check", default=None, type=click.Choice(["off", "warn", "strict"]), help="Repo size guardrail mode (default: warn)")
@click.option("--verbose", is_flag=True, default=False, help="Print full subprocess output")
@click.option("--print-config", is_flag=True, default=False, help="Print MCP client config snippets and exit")
def mcp(
    repo_path: str | None,
    db_path: str | None,
    served_repo_path: str | None,
    output_dir: str | None,
    repo_name: str | None,
    timeout: int | None,
    repo_size_check: str | None,
    verbose: bool,
    print_config: bool,
) -> None:
    """Run preflight + indexer and expose the resulting Kuzu DB as an MCP server."""
    from codedoc import log as _log

    if not repo_path and not db_path:
        raise click.UsageError("Provide REPO_PATH to index and serve, or use --db-path to serve an existing Kuzu DB.")

    if db_path:
        final_db_path = str(Path(db_path).resolve())
        effective_repo_path = str(Path(served_repo_path).resolve()) if served_repo_path else ""
    else:
        cfg = load_config({
            "output_dir": output_dir,
            "timeout": timeout,
            "repo_size_check": repo_size_check,
            "verbose": verbose or None,
        })
        state = run_mcp_pipeline(
            repo_path=repo_path or "",
            repo_name=repo_name,
            output_dir=cfg.output_dir,
            timeout=cfg.timeout,
            timeout_explicit=cfg.timeout_explicit,
            repo_size_check=cfg.repo_size_check,
            verbose=cfg.verbose,
            indexer_bin_dir=cfg.indexer_bin_dir,
        )
        if state.status != "done":
            _log.print_failure_panel(
                status=state.status,
                error=state.error,
                output_dir=state.output_dir or None,
                partial_artifacts=[],
            )
            sys.exit(1)
        final_db_path = state.kuzu_path
        effective_repo_path = state.repo_path

    command = format_mcp_command(final_db_path, effective_repo_path)
    snippets = format_client_snippets(final_db_path, effective_repo_path)
    _log.print_mcp_panel(final_db_path, command, snippets)

    if print_config:
        sys.exit(0)

    serve_mcp(final_db_path, repo_path=effective_repo_path, transport="stdio")


@main.command(name="mcp-http")
@click.argument("repo_path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option("--db-path", default=None, type=click.Path(exists=True), help="Serve an existing Kuzu DB directly")
@click.option("--repo-path", "served_repo_path", default=None, type=click.Path(exists=True, file_okay=False), help="Optional source repo path when serving an existing DB")
@click.option("--output-dir", type=click.Path(), default=None, help="Output directory (default: ./codedoc-output)")
@click.option("--repo-name", default=None, help="Override repository name used in output directory")
@click.option("--timeout", type=int, default=None, help="Per-stage timeout in seconds (default: 300)")
@click.option("--repo-size-check", default=None, type=click.Choice(["off", "warn", "strict"]), help="Repo size guardrail mode (default: warn)")
@click.option("--host", default="127.0.0.1", show_default=True, help="HTTP bind host")
@click.option("--port", type=int, default=8765, show_default=True, help="HTTP bind port")
@click.option("--path", default="/mcp", show_default=True, help="HTTP MCP path")
@click.option("--verbose", is_flag=True, default=False, help="Print full subprocess output")
@click.option("--print-config", is_flag=True, default=False, help="Print MCP client config snippets and exit")
def mcp_http(
    repo_path: str | None,
    db_path: str | None,
    served_repo_path: str | None,
    output_dir: str | None,
    repo_name: str | None,
    timeout: int | None,
    repo_size_check: str | None,
    host: str,
    port: int,
    path: str,
    verbose: bool,
    print_config: bool,
) -> None:
    """Run preflight + indexer and expose the resulting Kuzu DB over HTTP MCP."""
    from codedoc import log as _log

    if not repo_path and not db_path:
        raise click.UsageError("Provide REPO_PATH to index and serve, or use --db-path to serve an existing Kuzu DB.")

    if db_path:
        final_db_path = str(Path(db_path).resolve())
        effective_repo_path = str(Path(served_repo_path).resolve()) if served_repo_path else ""
    else:
        cfg = load_config({
            "output_dir": output_dir,
            "timeout": timeout,
            "repo_size_check": repo_size_check,
            "verbose": verbose or None,
        })
        state = run_mcp_http_pipeline(
            repo_path=repo_path or "",
            repo_name=repo_name,
            output_dir=cfg.output_dir,
            host=host,
            port=port,
            path=path,
            timeout=cfg.timeout,
            timeout_explicit=cfg.timeout_explicit,
            repo_size_check=cfg.repo_size_check,
            verbose=cfg.verbose,
            indexer_bin_dir=cfg.indexer_bin_dir,
        )
        if state.status != "done":
            _log.print_failure_panel(
                status=state.status,
                error=state.error,
                output_dir=state.output_dir or None,
                partial_artifacts=[],
            )
            sys.exit(1)
        final_db_path = state.kuzu_path
        effective_repo_path = state.repo_path

    url = format_mcp_http_url(host=host, port=port, path=path)
    command = format_mcp_http_command(
        final_db_path,
        repo_path=effective_repo_path,
        host=host,
        port=port,
        path=path,
    )
    docker_command = format_mcp_http_docker_command(
        final_db_path,
        repo_path=effective_repo_path,
        port=port,
        path=path,
    )
    snippets = format_http_client_snippets(url, final_db_path, effective_repo_path)
    _log.print_mcp_http_panel(final_db_path, url, command, docker_command, snippets)

    if print_config:
        sys.exit(0)

    serve_mcp(
        final_db_path,
        repo_path=effective_repo_path,
        transport="streamable-http",
        host=host,
        port=port,
        path=path,
    )


if __name__ == "__main__":
    main()
