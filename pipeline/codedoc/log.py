"""Pretty terminal output for the lumen pipeline.

All formatting lives here. Other modules import helpers from this module.
state.events always stores plain text — no ANSI codes.
Rich auto-degrades to plain text when stdout/stderr is not a TTY.
"""

from __future__ import annotations

from rich.box import SIMPLE_HEAVY
from rich.console import Console
from rich.console import Group
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
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

# Distinct accent color per analyst — used in live boxes, done lines, tool table
_ANALYST_COLORS: dict[str, str] = {
    "analyst/domain": "blue",
    "analyst/flows":  "cyan",
    "analyst/tech":   "yellow",
}

def _analyst_color(name: str) -> str:
    return _ANALYST_COLORS.get(name, "white")

# Emoji icon per stage number
_STAGE_ICONS: dict[int, str] = {
    1: "📦",
    2: "🤖",
    3: "🏗 ",
}

_indexer_live: Live | None = None
_indexer_state: dict[str, str] = {}
_agent_live: Live | None = None
_agent_state: dict[str, dict[str, object]] = {}
_workflow_state: dict[str, dict[str, object]] = {}
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
    intro = (
        "Illuminate any codebase. lumen reverse-engineers a source repository into a full "
        "documentation site — architecture diagrams, domain model, migration roadmap — "
        "without reading source files en masse."
    )
    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column()
    details.add_row("Repo", repo_name)
    details.add_row("Source", str(repo))
    details.add_row("Output", str(output_dir))
    header = Group(
        Text(_LUMEN_ASCII, style="bold cyan"),
        Text(intro, style="dim"),
        Text(""),
        details,
    )
    console.print()
    console.print(
        Panel(
            header,
            border_style="cyan",
            title="[bold cyan] Lumen [/bold cyan]",
        )
    )
    console.print(Rule(f"Lumen · {repo_name}", style="bold cyan"))


def print_stage_header(n: int, name: str) -> None:
    icon = _STAGE_ICONS.get(n, "▸")
    console.print()
    console.print(Rule(f"{icon} Stage {n}: {name}", style="cyan", align="left"))


def print_stage_done(n: int, name: str, elapsed: float) -> None:  # noqa: ARG001
    icon = _STAGE_ICONS.get(n, "▸")
    console.print(f"  [green]✓[/green] {icon} Stage {n} done  [green]{_fmt_elapsed(elapsed)}[/green]")


def _build_loc_breakdown_table(metrics: dict[str, object]) -> Table | None:
    loc_by_category = metrics.get("loc_by_category")
    files_by_category = metrics.get("files_by_category")
    loc_by_language = metrics.get("loc_by_language")
    files_by_language = metrics.get("files_by_language")

    if isinstance(loc_by_category, dict) and loc_by_category:
        loc_map = loc_by_category
        files_map = files_by_category if isinstance(files_by_category, dict) else {}
    elif isinstance(loc_by_language, dict) and loc_by_language:
        loc_map = loc_by_language
        files_map = files_by_language if isinstance(files_by_language, dict) else {}
    else:
        return None

    table = Table(box=SIMPLE_HEAVY, expand=True)
    table.add_column("Category", style="dim")
    table.add_column("LOC", justify="right")
    table.add_column("Files", justify="right")

    for key, loc in loc_map.items():
        table.add_row(str(key), f"{int(loc):,}", f"{int(files_map.get(key, 0) or 0):,}")
    return table


def print_repo_metrics_panel(metrics: dict[str, object], repo_size_check: str) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("Mode", str(repo_size_check))
    table.add_row("LOC", f"{metrics.get('total_loc', 0):,}")
    table.add_row("Files", f"{metrics.get('total_source_files', 0):,}")
    table.add_row("Band", str(metrics.get("size_band", "unknown")))
    table.add_row("Risk", str(metrics.get("risk_level", "unknown")))
    detected = metrics.get("detected_language_categories", metrics.get("detected_languages", []))
    if detected:
        table.add_row("Languages", ", ".join(str(x) for x in detected))
    repo_type = metrics.get("primary_repo_type", metrics.get("selected_archetype"))
    if repo_type:
        table.add_row("Repo Type", str(repo_type))
    capabilities = metrics.get("capabilities", [])
    if capabilities:
        table.add_row("Capabilities", ", ".join(str(x) for x in capabilities))
    breakdown = _build_loc_breakdown_table(metrics)
    warning = metrics.get("warning_message")
    renderables: list[object] = [table]
    if breakdown is not None:
        renderables.extend([Text("LOC Breakdown", style="bold"), breakdown])
    if warning:
        renderables.append(Text(str(warning), style="yellow"))
    renderable = table if len(renderables) == 1 else Group(*renderables)
    console.print()
    console.print(Panel(renderable, title="[bold yellow] Repo Metrics [/bold yellow]", border_style="yellow"))


def print_xlarge_mcp_guidance_panel(repo_path: str) -> None:
    lines = [
        "[bold yellow]Full pipeline stopped after preflight.[/bold yellow]",
        "This repo is classified as [bold]xlarge[/bold], so Lumen is recommending MCP mode instead of a full multi-agent docs run.",
        "",
        "[bold]What to do next[/bold]",
        "1. Start MCP mode. It will handle indexing and expose the graph tools:",
        f"   make lumen-docker-mcp REPO={repo_path}",
        "2. Wait for the MCP HTTP URL to appear in the terminal.",
        "3. Connect your LLM client to that MCP server.",
        "4. Ask focused questions against the indexed graph.",
        "",
        "[bold]Native alternative[/bold]",
        f"   make lumen-mcp REPO={repo_path}",
    ]
    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold yellow] XLarge Repo Guidance [/bold yellow]",
            border_style="yellow",
        )
    )


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
    if tag.startswith("architect"):
        update_workflow_phase("architect", status="running", tool=tool_name, turn=turn, max_turns=max_turns)
        return
    if tag.startswith("summary"):
        update_workflow_phase("summary", status="running", tool=tool_name, turn=turn, max_turns=max_turns)
        return
    text = Text(style="dim")
    text.append(f"  [{tag}]")
    text.append(f" {turn}/{max_turns}  ")
    text.append(tool_name)
    console.print(text)


def print_researcher_done(name: str, char_count: int) -> None:
    update_agent_box(name, status="done", artifacts=char_count)
    short = name.replace("researcher/", "").replace("analyst/", "")
    accent = _analyst_color(name)
    text = Text("  ")
    text.append(f"✓ {short}", style=f"bold {accent}")
    text.append("  researcher done  ", style="dim")
    text.append(f"{char_count:,}", style="bold")
    text.append(" chars", style="dim")
    console.print(text)


_NEW_TOOLS = {"get_workflows", "get_workflow_steps", "get_domains"}


def print_tool_usage_table(per_agent: dict[str, dict[str, int]]) -> None:
    """Print a per-researcher tool usage table.

    Highlights the new post-processing tools (get_workflows, get_workflow_steps,
    get_domains) so it's immediately visible whether they were called.
    """
    if not per_agent:
        return

    # Collect all tools used across all agents, sorted by total frequency desc
    all_tools: dict[str, int] = {}
    for counts in per_agent.values():
        for tool, n in counts.items():
            all_tools[tool] = all_tools.get(tool, 0) + n
    if not all_tools:
        return

    sorted_tools = sorted(all_tools.items(), key=lambda x: -x[1])
    agent_names = list(per_agent.keys())

    table = Table(
        title="Tool usage by researcher",
        box=SIMPLE_HEAVY,
        show_lines=False,
        title_style="bold",
        header_style="bold dim",
    )
    table.add_column("tool", style="", no_wrap=True)
    table.add_column("total", justify="right", style="dim")
    for name in agent_names:
        short = name.replace("analyst/", "").replace("researcher/", "")
        table.add_column(short, justify="right")

    for tool_name, total in sorted_tools:
        is_new = tool_name in _NEW_TOOLS
        name_cell = Text(tool_name, style="bold green" if is_new else "")
        if is_new:
            name_cell.append(" ✦", style="bold green")
        total_cell = Text(str(total), style="bold green" if is_new else "dim")
        per_agent_cells = []
        for name in agent_names:
            n = per_agent[name].get(tool_name, 0)
            cell = Text(str(n) if n else "·", style="bold green" if (n and is_new) else ("" if n else "dim"))
            per_agent_cells.append(cell)
        table.add_row(name_cell, total_cell, *per_agent_cells)

    console.print()
    console.print(table)


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
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()
    grid.add_row("artifacts", f"[bold]{artifacts}[/bold]")
    grid.add_row(
        "tokens",
        f"{input_tokens:,} in / {output_tokens:,} out"
        + (f"  [dim]({total:,} total)[/dim]" if total else ""),
    )
    grid.add_row("tool calls", str(tool_uses))
    console.print()
    console.print(Panel(grid, title="[bold] Run Summary [/bold]", border_style="dim", padding=(0, 1)))


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


def _status_renderable(status: str):
    if status == "running":
        return Spinner("dots", text=" running", style="cyan")
    char, style = {
        "pending": ("⊙", "dim"),
        "done":    ("✓", "green"),
        "failed":  ("✗", "red"),
    }.get(status, ("·", "dim"))
    return Text(f"{char} {status}", style=style)


def _render_agent_columns() -> Columns:
    panels: list[Panel] = []
    for name in ("analyst/domain", "analyst/flows", "analyst/tech"):
        info = _agent_state.get(name, {})
        label = name.replace("analyst/", "") + " researcher"
        status = str(info.get("status", "pending"))
        turn = info.get("turn")
        max_turns = info.get("max_turns")
        tool = str(info.get("tool", "waiting"))
        artifacts = info.get("artifacts")
        accent = _analyst_color(name)
        body: list[object] = [Text("status: "), _status_renderable(status)]
        if turn and max_turns:
            body.append(Text(f"turn: {turn}/{max_turns}", style="dim"))
        # Active tool shown in accent color when running, dim otherwise
        tool_style = f"bold {accent}" if status == "running" else "dim"
        body.append(Text(f"tool: {tool}", style=tool_style))
        if artifacts is not None:
            body.append(Text(f"artifacts: {artifacts}", style="dim"))
        # Border uses accent when running, green when done, dark-gray when pending.
        # "dim" made pending boxes nearly invisible; "bright_black" keeps them framed.
        border = {
            "running": accent,
            "done":    "green",
            "failed":  "red",
            "pending": "bright_black",
        }.get(status, "bright_black")
        title_style = f"bold {accent}" if status == "running" else "bold"
        panels.append(Panel(
            Group(*body),
            title=f"[{title_style}]{label}[/{title_style}]",
            border_style=border,
        ))
    return Columns(panels, equal=True, expand=True)


def _render_workflow_panel() -> Panel:
    table = Table(box=SIMPLE_HEAVY, expand=True)
    table.add_column("Phase", style="bold")
    table.add_column("Status")
    table.add_column("Activity", overflow="fold")
    table.add_column("Progress", justify="right")
    for key, label in (
        ("synthesis", "synthesis"),
        ("architect", "architect"),
        ("summary", "summary"),
    ):
        info = _workflow_state.get(key, {})
        status = str(info.get("status", "pending"))
        tool = str(info.get("tool", "waiting"))
        turn = info.get("turn")
        max_turns = info.get("max_turns")
        progress = f"{turn}/{max_turns}" if turn and max_turns else ""
        table.add_row(label, _status_renderable(status), tool, progress)
    return Panel(table, title="Workflow", border_style="magenta")


def _render_agent_dashboard():
    return Group(_render_agent_columns(), _render_workflow_panel())


def start_agent_boxes() -> None:
    global _agent_live, _agent_state, _workflow_state
    if not console.is_terminal:
        return
    _agent_state = {
        "analyst/domain": {"status": "pending", "tool": "waiting"},
        "analyst/flows": {"status": "pending", "tool": "waiting"},
        "analyst/tech": {"status": "pending", "tool": "waiting"},
    }
    _workflow_state = {
        "synthesis": {"status": "pending", "tool": "waiting"},
        "architect": {"status": "pending", "tool": "waiting"},
        "summary": {"status": "pending", "tool": "waiting"},
    }
    _agent_live = Live(_render_agent_dashboard(), console=console, refresh_per_second=6, transient=False)
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
        _agent_live.update(_render_agent_dashboard())


def update_workflow_phase(
    name: str,
    *,
    status: str | None = None,
    tool: str | None = None,
    turn: int | None = None,
    max_turns: int | None = None,
) -> None:
    if name not in _workflow_state:
        _workflow_state[name] = {"status": "pending", "tool": "waiting"}
    if status is not None:
        _workflow_state[name]["status"] = status
    if tool is not None:
        _workflow_state[name]["tool"] = tool
    if turn is not None:
        _workflow_state[name]["turn"] = turn
    if max_turns is not None:
        _workflow_state[name]["max_turns"] = max_turns
    if _agent_live is not None:
        _agent_live.update(_render_agent_dashboard())


def stop_agent_boxes() -> None:
    global _agent_live, _agent_state, _workflow_state
    if _agent_live is not None:
        _agent_live.stop()
    _agent_live = None
    _agent_state = {}
    _workflow_state = {}


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

    title = "[bold red] Pipeline Failed [/bold red]"
    border_style = "red"
    if status == "stopped":
        title = "[bold yellow] Pipeline Stopped [/bold yellow]"
        border_style = "yellow"

    _err_console.print()
    _err_console.print(
        Panel("\n".join(lines), title=title, border_style=border_style)
    )


def print_mcp_panel(db_path: str, command: str, snippets: dict[str, str]) -> None:
    from codedoc.mcp_server import format_server_identity
    identity = format_server_identity(db_path)
    lines = [
        f"[dim]Repo    [/dim]  {identity['repo_name']}",
        f"[dim]Kuzu DB [/dim]  {db_path}",
        f"[dim]Command [/dim]  {command}",
        "[dim]Tip     [/dim]  Call the MCP tool `server_info` to confirm the active repo after reconnects.",
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
    from codedoc.mcp_server import format_server_identity
    identity = format_server_identity(db_path)
    lines = [
        f"[dim]Repo[/dim]    {identity['repo_name']}",
        f"[dim]DB[/dim]      {db_path}",
        f"[dim]URL[/dim]     {url}",
        f"[dim]Server[/dim]  {identity['server_name']}",
        "[dim]Tip[/dim]     Call the MCP tool `server_info` to verify the active repo after switching services.",
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
