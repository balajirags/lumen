"""Pretty terminal output for the lumen pipeline.

All formatting lives here. Other modules import helpers from this module.
state.events always stores plain text — no ANSI codes.
Rich auto-degrades to plain text when stdout/stderr is not a TTY.
"""

from __future__ import annotations

from rich.console import Console
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console(highlight=False)
_err_console = Console(stderr=True, highlight=False)

# Stage label → rich style
_STAGE_STYLES: dict[str, str] = {
    "pipeline":    "bold blue",
    "indexer":     "cyan",
    "agent":       "green",
    "builder":     "blue",
    "supervisor":  "bold yellow",
    "synthesizer": "magenta",
    "researcher":  "cyan",
}

_indexer_live: Live | None = None
_indexer_state: dict[str, str] = {}
_agent_live: Live | None = None
_agent_state: dict[str, dict[str, object]] = {}
_LUMEN_ASCII = "\n".join([
    " _                              ",
    "| |    _   _ _ __ ___   ___ _ __ ",
    "| |   | | | | '_ ` _ \\ / _ \\ '_ \\",
    "| |___| |_| | | | | | |  __/ | | |",
    "|_____|\__,_|_| |_| |_|\\___|_| |_|",
])


def _stage_style(stage: str) -> str:
    for key, style in _STAGE_STYLES.items():
        if stage.startswith(key):
            return style
    return "bold"


def _fmt_elapsed(seconds: float) -> str:
    if seconds >= 60:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    return f"{seconds:.1f}s"


# ---------------------------------------------------------------------------
# Generic log line (used by state.log)
# ---------------------------------------------------------------------------

def log_to_console(stage: str, message: str, ts: str) -> None:
    """Render a timestamped log line with colour."""
    text = Text()
    text.append(f"[{ts}]", style="dim")
    text.append(" ")
    text.append(f"[{stage}]", style=_stage_style(stage))
    text.append(f" {message}")
    console.print(text)


# ---------------------------------------------------------------------------
# Pipeline-level banners
# ---------------------------------------------------------------------------

def print_pipeline_start(repo: str, output_dir: str) -> None:  # noqa: ARG001
    from pathlib import Path
    repo_name = Path(repo).name
    console.print()
    console.print(
        Panel(
            f"[bold cyan]{_LUMEN_ASCII}[/bold cyan]\n[dim]Analyzing[/dim] {repo_name}",
            border_style="cyan",
        )
    )
    console.print(Rule(f"Lumen · {repo_name}", style="bold cyan"))


def print_stage_header(n: int, name: str) -> None:
    console.print()
    console.print(Rule(f"Stage {n}: {name}", style="cyan", align="left"))


def print_stage_done(n: int, name: str, elapsed: float) -> None:  # noqa: ARG001
    console.print(f"  [green]✓[/green] Stage {n} done  [green]{_fmt_elapsed(elapsed)}[/green]")


def print_repo_metrics_panel(metrics: dict[str, object], repo_size_check: str) -> None:
    lines: list[str] = []
    lines.append(f"[dim]Mode      [/dim]  {repo_size_check}")
    lines.append(f"[dim]LOC       [/dim]  {metrics.get('total_loc', 0):,}")
    lines.append(f"[dim]Files     [/dim]  {metrics.get('total_source_files', 0):,}")
    lines.append(f"[dim]Band      [/dim]  {metrics.get('size_band', 'unknown')}")
    lines.append(f"[dim]Risk      [/dim]  {metrics.get('risk_level', 'unknown')}")
    detected = metrics.get("detected_languages", [])
    if detected:
        lines.append(f"[dim]Languages [/dim]  {', '.join(str(x) for x in detected)}")
    warning = metrics.get("warning_message")
    if warning:
        lines.append("")
        lines.append(f"[yellow]{warning}[/yellow]")
    console.print()
    console.print(Panel("\n".join(lines), title="[bold yellow] Repo Metrics [/bold yellow]", border_style="yellow"))


# ---------------------------------------------------------------------------
# Supervisor / agent lines
# ---------------------------------------------------------------------------

def print_supervisor_line(msg: str) -> None:
    """Bold print for supervisor status lines (no timestamp)."""
    console.print(Text(msg, style="bold"))


def print_progress_line(tag: str, turn: int, max_turns: int, tool_name: str) -> None:
    """Dim turn-level progress line emitted by run_loop."""
    if tag.startswith("analyst/"):
        update_agent_box(tag, status="running", turn=turn, max_turns=max_turns, tool=tool_name)
        return
    text = Text(style="dim")
    text.append(f"  [{tag}]")
    text.append(f" {turn}/{max_turns}  ")
    text.append(tool_name)
    console.print(text)


def print_researcher_done(name: str, char_count: int) -> None:
    update_agent_box(name, status="done", artifacts=char_count)
    label = name.replace("researcher/", "").replace("analyst/", "")
    text = Text("  ")
    text.append(label, style="bold green")
    text.append(f"  done — ")
    text.append(f"{char_count:,} chars", style="dim")
    console.print(text)


def print_synthesizer_done(artifact_count: int) -> None:
    text = Text("  ")
    text.append("synthesizer", style="bold magenta")
    text.append("  done — ")
    text.append(f"{artifact_count} artifact(s)", style="bold green")
    console.print(text)


def print_supervisor_summary(
    artifacts: int,
    input_tokens: int,
    output_tokens: int,
    tool_uses: int,
) -> None:
    total = input_tokens + output_tokens
    console.print(
        f"  [dim]artifacts :[/dim] {artifacts}\n"
        f"  [dim]tokens    :[/dim] {input_tokens:,} in / {output_tokens:,} out  "
        f"[dim]({total:,} total)[/dim]\n"
        f"  [dim]tool uses :[/dim] {tool_uses}"
    )


def _render_indexer_panel() -> Panel:
    lines: list[str] = []
    for language, status in _indexer_state.items():
        style = {
            "pending": "dim",
            "running": "bold cyan",
            "done": "green",
            "failed": "red",
        }.get(status, "dim")
        lines.append(f"[{style}]{language:<24} {status}[/{style}]")
    if not lines:
        lines.append("[dim]Preparing indexers…[/dim]")
    return Panel("\n".join(lines), title="Indexer Progress", border_style="cyan")


def start_indexer_progress(languages: list[str]) -> None:
    global _indexer_live, _indexer_state
    if not console.is_terminal:
        return
    _indexer_state = {language: "pending" for language in languages}
    _indexer_live = Live(_render_indexer_panel(), console=console, refresh_per_second=6, transient=False)
    _indexer_live.start()


def update_indexer_progress(language: str, status: str) -> None:
    if language not in _indexer_state:
        _indexer_state[language] = status
    else:
        _indexer_state[language] = status
    if _indexer_live is not None:
        _indexer_live.update(_render_indexer_panel())


def stop_indexer_progress() -> None:
    global _indexer_live, _indexer_state
    if _indexer_live is not None:
        _indexer_live.stop()
    _indexer_live = None
    _indexer_state = {}


def _render_agent_columns() -> Columns:
    panels: list[Panel] = []
    for name in ("analyst/domain", "analyst/flows", "analyst/tech"):
        info = _agent_state.get(name, {})
        label = name.replace("analyst/", "")
        status = str(info.get("status", "pending"))
        turn = info.get("turn")
        max_turns = info.get("max_turns")
        tool = str(info.get("tool", "waiting"))
        artifacts = info.get("artifacts")
        body: list[str] = [f"status: {status}"]
        if turn and max_turns:
            body.append(f"turn: {turn}/{max_turns}")
        body.append(f"tool: {tool}")
        if artifacts is not None:
            body.append(f"artifacts: {artifacts}")
        border = {
            "pending": "dim",
            "running": "cyan",
            "done": "green",
            "failed": "red",
        }.get(status, "dim")
        panels.append(Panel("\n".join(body), title=label, border_style=border))
    return Columns(panels, equal=True, expand=True)


def start_agent_boxes() -> None:
    global _agent_live, _agent_state
    if not console.is_terminal:
        return
    _agent_state = {
        "analyst/domain": {"status": "pending", "tool": "waiting"},
        "analyst/flows": {"status": "pending", "tool": "waiting"},
        "analyst/tech": {"status": "pending", "tool": "waiting"},
    }
    _agent_live = Live(_render_agent_columns(), console=console, refresh_per_second=6, transient=False)
    _agent_live.start()


def update_agent_box(
    name: str,
    *,
    status: str | None = None,
    turn: int | None = None,
    max_turns: int | None = None,
    tool: str | None = None,
    artifacts: int | None = None,
) -> None:
    if name not in _agent_state:
        _agent_state[name] = {"status": "pending", "tool": "waiting"}
    if status is not None:
        _agent_state[name]["status"] = status
    if turn is not None:
        _agent_state[name]["turn"] = turn
    if max_turns is not None:
        _agent_state[name]["max_turns"] = max_turns
    if tool is not None:
        _agent_state[name]["tool"] = tool
    if artifacts is not None:
        _agent_state[name]["artifacts"] = artifacts
    if _agent_live is not None:
        _agent_live.update(_render_agent_columns())


def stop_agent_boxes() -> None:
    global _agent_live, _agent_state
    if _agent_live is not None:
        _agent_live.stop()
    _agent_live = None
    _agent_state = {}


# ---------------------------------------------------------------------------
# Final CLI panels
# ---------------------------------------------------------------------------

def print_success_panel(
    output_dir: str,
    site_path: str | None,
    artifacts_dir: str | None,
    artifact_count: int,
    input_tokens: int,
    output_tokens: int,
    tool_uses: int,
    elapsed: float,
) -> None:
    total = input_tokens + output_tokens
    lines: list[str] = []
    lines.append(f"[dim]Output dir[/dim]  {output_dir}")
    if site_path:
        lines.append(f"[dim]Doc-site  [/dim]  {site_path}")
    if artifacts_dir:
        lines.append(f"[dim]Artifacts [/dim]  {artifact_count} file(s) in {artifacts_dir}")
    lines.append(f"[dim]Details   [/dim]  {output_dir}/pipeline.json")
    if input_tokens or output_tokens or tool_uses:
        lines.append("")
        lines.append(
            f"[dim]Tokens    [/dim]  {input_tokens:,} in / {output_tokens:,} out"
            + (f"  [dim]({total:,} total)[/dim]" if total else "")
        )
        lines.append(f"[dim]Tool uses [/dim]  {tool_uses}")
    if elapsed:
        lines.append(f"[dim]Elapsed   [/dim]  {_fmt_elapsed(elapsed)}")

    console.print()
    console.print(Panel("\n".join(lines), title="[bold green] Done [/bold green]", border_style="green"))


def print_failure_panel(
    status: str,
    error: str | None,
    output_dir: str | None,
    partial_artifacts: list[str],
) -> None:
    lines: list[str] = []
    lines.append(f"[dim]Status [/dim]  {status}")
    if error:
        lines.append(f"[red]Error  [/red]  {error}")
    if output_dir:
        lines.append(f"[dim]Details[/dim]  {output_dir}/pipeline.json")
    if partial_artifacts:
        lines.append("")
        lines.append(f"[dim]Partial artifacts ({len(partial_artifacts)} file(s)):[/dim]")
        for a in partial_artifacts:
            lines.append(f"  {a}")

    _err_console.print()
    _err_console.print(
        Panel("\n".join(lines), title="[bold red] Pipeline Failed [/bold red]", border_style="red")
    )


def print_mcp_panel(db_path: str, command: str, snippets: dict[str, str]) -> None:
    lines = [
        f"[dim]Kuzu DB [/dim]  {db_path}",
        f"[dim]Command [/dim]  {command}",
    ]
    console.print()
    console.print(Panel("\n".join(lines), title="[bold cyan] MCP Ready [/bold cyan]", border_style="cyan"))


def print_mcp_http_panel(
    db_path: str,
    url: str,
    command: str,
    docker_command: str,
    snippets: dict[str, str],
) -> None:
    lines = [
        f"[dim]DB[/dim]      {db_path}",
        f"[dim]URL[/dim]     {url}",
        "",
        "[bold]Native[/bold]",
        command,
        "",
        "[bold]Docker[/bold]",
        docker_command,
    ]
    for title, snippet in snippets.items():
        lines.extend(["", f"[bold]{title}[/bold]", snippet])
    console.print()
    console.print(Panel("\n".join(lines), title="[bold cyan] MCP HTTP Ready [/bold cyan]", border_style="cyan"))
    for name, snippet in snippets.items():
        console.print()
        console.print(Panel(snippet, title=f"[bold]{name}[/bold]", border_style="blue"))
