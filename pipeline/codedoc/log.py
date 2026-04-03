"""Pretty terminal output for the lumen pipeline.

All formatting lives here. Other modules import helpers from this module.
state.events always stores plain text — no ANSI codes.
Rich auto-degrades to plain text when stdout/stderr is not a TTY.
"""

from __future__ import annotations

from rich.console import Console
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
    console.print(Rule(f"lumen · {repo_name}", style="bold cyan"))


def print_stage_header(n: int, name: str) -> None:
    console.print()
    console.print(Rule(f"Stage {n}: {name}", style="cyan", align="left"))


def print_stage_done(n: int, name: str, elapsed: float) -> None:  # noqa: ARG001
    console.print(f"  [green]✓[/green] Stage {n} done  [green]{_fmt_elapsed(elapsed)}[/green]")


# ---------------------------------------------------------------------------
# Supervisor / agent lines
# ---------------------------------------------------------------------------

def print_supervisor_line(msg: str) -> None:
    """Bold print for supervisor status lines (no timestamp)."""
    console.print(Text(msg, style="bold"))


def print_progress_line(tag: str, turn: int, max_turns: int, tool_name: str) -> None:
    """Dim turn-level progress line emitted by run_loop."""
    text = Text(style="dim")
    text.append(f"  [{tag}]")
    text.append(f" {turn}/{max_turns}  ")
    text.append(tool_name)
    console.print(text)


def print_researcher_done(name: str, char_count: int) -> None:
    label = name.replace("researcher/", "")
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
