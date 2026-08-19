# Adding a new Lumen pipeline

This document is both **reference material** and a **prompt template**. Give it to an
engineer or an AI coding agent along with a filled-in [Your task](#your-task) section, and
the result should be a new `lumen <name>` pipeline that looks and behaves exactly like every
other one — same file layout, same naming, same make/Docker/native wiring, same
verification steps. The goal is predictability: two different people implementing two
different pipelines from this document should produce structurally identical diffs.

The reference implementation for everything described here is the `security-audit`
pipeline, added in this exact way. When in doubt, go read its files instead of guessing:

- `pipeline/codedoc/pipelines/security_audit.py`
- `pipeline/codedoc/stages/security_audit_agent.py`
- `pipeline/codedoc/stages/parallel.py`
- `pipeline/codedoc/prompts/security-analyst-access.md`, `security-analyst-dependencies.md`, `security-threat-model.md`, `security-synthesis.md`
- `pipeline/codedoc/cli.py` (the `common_pipeline_options` decorator and the `security_audit` command)
- `Makefile` (`lumen-security-audit`, `lumen-docker-security-audit` targets)
- `scripts/lumen-docker-security-audit.sh`

---

## Mental model (read this before writing any code)

```
Source repo → [preflight + indexer]  ← SHARED, NEVER MODIFIED for a new pipeline
                    │
                    ▼
              KuzuDB graph
                    │
                    ▼
        [your new agent stage]        ← THIS is the only pluggable part
          fan-out (N parallel roles, each querying the graph + writing 1 artifact)
                    │
                    ▼
          fan-in (1 synthesis pass reading the fan-out artifacts off disk)
```

- **Preflight and indexer are shared and permanent.** Every pipeline reuses
  `run_preflights` and `run_indexer` completely unchanged. Never edit
  `preflight/*` or `stages/indexer.py` to add a pipeline.
- **Only the agent stage (stage 2) is pluggable.** A new pipeline gets its own
  `run_agent(state) -> state` function in a new `stages/<name>_agent.py` module.
  `stages/agent.py` (the docs pipeline's `run_supervisor_agent`, its archetype system, its
  artifact planner) is never touched or generalized for a new pipeline — it has its own
  contract that isn't worth coupling to.
- **The required shape is fan-out then fan-in**: 2+ roles run in parallel, each querying the
  graph independently and writing its own findings artifact, then exactly one synthesis
  step reads those artifacts off disk and writes the final output. This mirrors the docs
  pipeline's Phase 2 (parallel analysts) → Phase 3 (architect) shape, just without the
  archetype/artifact-plan machinery.
- **No shared plugin registry, no "PipelineSpec" abstraction.** A new pipeline is a plain
  Python module built directly on the primitives below. Copying the reference
  implementation and renaming things is the intended workflow — don't build a framework on
  top of this document's pattern.

---

## Reusable building blocks (do not reinvent these)

| Building block | Where | What it does |
|---|---|---|
| `KuzuBackend(kuzu_path)` | `codedoc.kg_tools` | Opens the indexed graph (read-only by default). Instantiate a fresh one per thread/task — connections are not thread-safe. |
| `ReverseEngineerToolkit(backend, repo_path=...)` | `codedoc.kg_tools` | The full graph query surface (40+ tools: `get_schema`, `get_entry_points`, `get_api_endpoints`, `get_external_dependencies`, `get_hotspots`, `get_domains`, `get_workflows`, `get_unused_code`, etc. — see `kg_tools/toolkit.py` for the complete list). |
| `create_provider(provider, model, base_url, num_ctx)` | `codedoc.llm` | Builds the LLM provider (Claude/Ollama/OpenAI) from CLI/config values. Call it once per pipeline run, share the same instance across fan-out tasks. |
| `run_loop(provider, toolkit, system_prompt, user_request, output_root, ...)` | `codedoc.stages.agent` | The generic agentic tool loop: gives the model graph tools + `write_artifact`, runs until it stops or hits `max_turns`. Key kwargs: `allowed_artifact_paths` (a `set[str]` — the ONLY filenames this call may write; anything else is rejected with a correction hint), `phase_label` (prefix for log lines, e.g. `"<name>/<role>"`), `max_source_reads=0` (analysts should reason over graph structure, not raw source), `include_write_artifact=True`. Returns a dict: `{"status", "error", "artifacts", "events", "tool_uses", "tool_call_counts", "input_tokens", "output_tokens"}`. |
| `run_parallel_tasks(tasks: dict[str, Callable[[], dict]])` | `codedoc.stages.parallel` | The fan-out/fan-in concurrency helper. Give it a dict of no-arg callables (each one builds its own `KuzuBackend`/`ReverseEngineerToolkit` and calls `run_loop`), get back a dict of their result dicts. |
| `common_pipeline_options` | `codedoc.cli` | Click decorator bundling the ~10 shared options (`--output-dir`, `--model`, `--provider`, `--max-turns`, `--allow-xlarge`, etc.) that every agent-stage pipeline command exposes identically. |
| `create_run_dir`, `init_state`, `log_pipeline_start`, `finalize_state`, `apply_repo_size_runtime_defaults(state, bump_max_turns=...)`, `should_stop_for_xlarge_repo(state)` | `codedoc.pipelines.common` | Shared run-dir/state/finalization plumbing every pipeline module calls in the same order. Mode-agnostic — no code here needs editing when you add a pipeline. |
| `start_agent_boxes(agent_names=[...], workflow_phases=[...])`, `update_agent_box(name, status=, tool=, artifacts=)`, `update_workflow_phase(name, status=, tool=)`, `print_researcher_done(name, n_artifacts)`, `print_tool_usage_table(per_role_tool_counts)`, `print_synthesizer_done(n_artifacts)`, `stop_agent_boxes()` | `codedoc.log` | The live fan-out/fan-in dashboard `lumen run` uses. **Data-driven, not hardcoded** — pass your own role names/phase names to `start_agent_boxes()` and every downstream call (including the per-turn progress line `run_loop` already emits via `phase_label`) routes into your boxes automatically. Skipping this wiring is the #1 way a new pipeline's console output ends up looking inconsistent with `lumen run`. |

## What NOT to touch

- `pipeline/codedoc/stages/agent.py` — the docs pipeline's supervisor, prompts, and
  artifact-plan logic. Reuse `run_loop` (a plain function it exports); don't modify anything
  else in this file.
- `pipeline/codedoc/archetype_registry.py`, `pipeline/codedoc/artifact_planner.py` — the
  docs pipeline's archetype/artifact-plan system. Your pipeline defines its own small
  `allowed_artifact_paths` sets directly; it does not need or use this system.
- `pipeline/codedoc/stages/builder.py` — the MkDocs build step. Only call it from your
  pipeline module if you specifically want an MkDocs site; most non-docs pipelines skip it
  and leave artifacts as plain markdown (see `security_audit.py`, which has no Stage 3).
- `pipeline/codedoc/preflight/*`, `pipeline/codedoc/stages/indexer.py` — shared and
  unmodified, as above.
- `Dockerfile`, `scripts/build-native.sh`, `scripts/install-lumen.sh`, the native `lumen`
  launcher template — all already subcommand-agnostic. A new pipeline needs **zero**
  changes here (see the Make/Docker/native section below).

## Naming conventions

Pick one `<name>` in kebab-case for your pipeline (e.g. `security-audit`,
`test-coverage`, `dependency-graph`). Derive everything else from it mechanically:

| Thing | Convention | Example |
|---|---|---|
| CLI command | `<name>` (kebab-case, as-is) | `lumen security-audit` |
| `mode` passed to `init_state` | `<name>` (kebab-case, as-is) | `"security-audit"` |
| Pipeline module | `pipelines/<name_snake>.py` | `pipelines/security_audit.py` |
| Agent-stage module | `stages/<name_snake>_agent.py` | `stages/security_audit_agent.py` |
| Prompt files | `prompts/<name>-<role>.md`, `prompts/<name>-synthesis.md` | `prompts/security-analyst-access.md`, `prompts/security-synthesis.md` |
| `phase_label` per `run_loop` call | `<short-name>/<role>` | `"security/access"`, `"security/synthesis"` |
| Artifact paths | `<short-name>/<artifact>.md`, one folder per pipeline | `security/access-control-findings.md` |
| Makefile targets | `lumen-<name>` (native), `lumen-docker-<name>` (Docker) | `lumen-security-audit`, `lumen-docker-security-audit` |
| Docker wrapper script | `scripts/lumen-docker-<name>.sh` | `scripts/lumen-docker-security-audit.sh` |

(`<name_snake>` is `<name>` with hyphens replaced by underscores, since it's a Python
module name.)

---

## Step-by-step

### 1. Design the fan-out/fan-in shape

Before writing code, decide:
- How many parallel roles (2–4 is typical; it does not have to be 3 just because the docs
  pipeline uses 3 analysts).
- What each role's single findings artifact is named.
- What the one fan-in/synthesis step reads and produces.
- Whether you need a Stage 3 (MkDocs build) at all — most non-docs pipelines don't.

### 2. Write the prompts

One markdown prompt per role, plus one synthesis prompt, under `pipeline/codedoc/prompts/`.
Follow the existing style (see `security-analyst-access.md` for a role prompt,
`security-synthesis.md` for the fan-in prompt):

- A `## Your Artifact` section with the exact filename and a fenced template of its
  expected structure.
- An evidence model: role prompts tag findings `[Observed]` (from a tool call) vs.
  `[Inferred]` (reasoned); the synthesis prompt tags everything `[Synthesized]`.
- Explicit tool names to call (pull real names from `kg_tools/toolkit.py`, don't invent
  ones) and a line count budget (`≤ 80 lines total`, etc.) to keep output focused.
- The synthesis prompt must say "You do NOT call any graph query tools. You only call
  `write_artifact`." — it receives role artifacts injected as text, it doesn't query the
  graph itself.

### 3. Write the agent-stage module: `stages/<name_snake>_agent.py`

**Wire in the pretty logging — this is not optional.** `run_loop` alone does not make your
pipeline's console output look like `lumen run`'s. You must explicitly call the
`codedoc.log` dashboard functions with your own role/phase names (see the table above and
the skeleton below): `start_agent_boxes(agent_names=..., workflow_phases=...)` before
fan-out, `update_agent_box(...)` when each role starts/finishes, `print_researcher_done`
+ `print_tool_usage_table` after fan-out, `update_workflow_phase(...)` +
`print_synthesizer_done` around the fan-in call, and `stop_agent_boxes()` on every exit
path (success, failure, and exceptions — use `try`/`except` around the fan-out and fan-in
calls as shown below so a crash doesn't leave a half-finished live dashboard on screen).
Skipping this was a real regression once already: a pipeline built without it fell back to
plain dim-text lines with no live boxes, no "researcher done" checkmarks, and no tool-usage
table — visibly inconsistent with `lumen run`, and it had to be retrofitted afterward.
Do it right the first time.

Skeleton (copy `stages/security_audit_agent.py` and rename):

```python
"""Stage 2 (alternative) — <Name> agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codedoc.kg_tools import KuzuBackend, ReverseEngineerToolkit
from codedoc.llm import create_provider
from codedoc.stages.agent import run_loop
from codedoc.stages.parallel import run_parallel_tasks

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_ROLE_A_ARTIFACT = "<short-name>/<role-a>-findings.md"
_ROLE_B_ARTIFACT = "<short-name>/<role-b>-findings.md"
_REPORT_ARTIFACT = "<short-name>/report.md"

_ROLE_PROMPT_FILES: dict[str, str] = {
    "<short-name>/<role-a>": "<name>-<role-a>.md",
    "<short-name>/<role-b>": "<name>-<role-b>.md",
}
_ROLE_ARTIFACTS: dict[str, set[str]] = {
    "<short-name>/<role-a>": {_ROLE_A_ARTIFACT},
    "<short-name>/<role-b>": {_ROLE_B_ARTIFACT},
}


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


def _orientation_summary(kuzu_path: str, repo_path: str) -> str:
    backend = KuzuBackend(kuzu_path)
    toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
    try:
        return toolkit.call("get_architecture_summary")
    except Exception as e:
        return f"[Orientation data unavailable — graph query failed ({e}).]"


def _make_role_task(name, *, provider, kuzu_path, repo_path, repo_name,
                     orientation_summary, artifacts_dir, max_turns,
                     max_context_tokens, use_anthropic_format, verbose):
    def _task() -> dict:
        backend = KuzuBackend(kuzu_path)
        toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
        system_prompt = (
            _load_prompt(_ROLE_PROMPT_FILES[name])
            + f"\n\n---\n## Orientation Summary\n\n{orientation_summary}\n"
            + f"\n\n---\n## Runtime context\n\n- Repository name: `{repo_name}`\n"
        )
        return run_loop(
            provider=provider, toolkit=toolkit, system_prompt=system_prompt,
            user_request="Begin your review and write your findings artifact.",
            output_root=artifacts_dir, max_turns=max_turns, verbose=verbose,
            use_anthropic_format=use_anthropic_format,
            max_context_tokens=max_context_tokens, max_source_reads=0,
            include_write_artifact=True, phase_label=name,
            allowed_artifact_paths=_ROLE_ARTIFACTS[name],
        )
    return _task


def _build_synthesis_prompt(artifacts_dir: str, repo_name: str) -> str:
    prompt = _load_prompt("<name>-synthesis.md")
    prompt += "\n\n---\n## Findings (written by reviewers)\n\n"
    for rel_path in (_ROLE_A_ARTIFACT, _ROLE_B_ARTIFACT):
        full_path = Path(artifacts_dir) / rel_path
        content = full_path.read_text(encoding="utf-8") if full_path.exists() else "_Not produced._"
        prompt += f"### {rel_path}\n\n{content}\n\n"
    prompt += (
        f"\n\n---\n## Runtime context\n\n- Repository name: `{repo_name}`\n"
        f"- Write only `{_REPORT_ARTIFACT}` using the `write_artifact` tool.\n"
        "- Do NOT call any graph query tools.\n"
    )
    return prompt


def run_agent(state) -> Any:
    """Pipeline stage 2 for the <name> pipeline. PipelineState in, PipelineState out."""
    kuzu_path, repo_path, output_dir = state.kuzu_path, state.repo_path, state.output_dir
    model, provider_name, base_url = state.model, state.provider, state.base_url
    max_turns, max_context_tokens = state.max_turns, state.max_context_tokens
    ollama_num_ctx = state.ollama_num_ctx or 131_072
    verbose = state.verbose
    repo_name = state.repo_name or (Path(repo_path).name if repo_path else "unknown")

    try:
        KuzuBackend(kuzu_path)
    except FileNotFoundError as e:
        state.status, state.error = "failed", str(e)
        return state

    provider = create_provider(provider=provider_name, model=model, base_url=base_url, num_ctx=ollama_num_ctx)
    use_anthropic_format = provider_name in ("claude", "anthropic") or (
        provider_name == "auto" and (model.startswith("claude") or model.startswith("anthropic"))
    )
    artifacts_dir = str(Path(output_dir) / "artifacts")
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    events, input_tokens, output_tokens, tool_uses = [], 0, 0, 0

    from codedoc import log as _log

    def log(msg: str) -> None:
        events.append(msg)
        _log.print_supervisor_line(msg)

    log("[<name>] Phase 1 — orientation (direct toolkit query)")
    orientation_summary = _orientation_summary(kuzu_path, repo_path)

    role_turns = max(10, max_turns // 3)
    synthesis_turns = max(6, max_turns // 4)
    role_names = list(_ROLE_PROMPT_FILES)

    log(f"[<name>] Phase 2 — spawning {len(role_names)} roles in parallel")
    # start_agent_boxes() is what makes this pipeline's console output look like
    # `lumen run`'s — pass YOUR role/phase names, don't skip this call.
    _log.start_agent_boxes(agent_names=role_names, workflow_phases=["<short-name>/synthesis"])
    for name in role_names:
        _log.update_agent_box(name, status="running", tool="starting")

    tasks = {
        name: _make_role_task(
            name, provider=provider, kuzu_path=kuzu_path, repo_path=repo_path,
            repo_name=repo_name, orientation_summary=orientation_summary,
            artifacts_dir=artifacts_dir, max_turns=role_turns,
            max_context_tokens=max_context_tokens,
            use_anthropic_format=use_anthropic_format, verbose=verbose,
        )
        for name in role_names
    }
    try:
        results = run_parallel_tasks(tasks)
    except Exception:
        _log.stop_agent_boxes()
        raise

    all_artifacts = []
    role_tool_counts = {}
    for name, result in results.items():
        events.extend(result["events"])
        input_tokens += result["input_tokens"]
        output_tokens += result["output_tokens"]
        tool_uses += result["tool_uses"]
        role_tool_counts[name] = result.get("tool_call_counts", {})
        n_artifacts = len(result["artifacts"])
        if result["status"] == "failed":
            _log.update_agent_box(name, status="failed", tool="error")
            log(f"[<name>] WARNING: {name} failed: {result['error']}")
        else:
            all_artifacts.extend(result["artifacts"])
            _log.update_agent_box(name, status="done", tool="complete", artifacts=n_artifacts)
            log(f"[<name>] {name} done — {n_artifacts} artifact(s)")
        _log.print_researcher_done(name, n_artifacts)
    _log.print_tool_usage_table(role_tool_counts)

    log("[<name>] Phase 3 — running synthesis…")
    _log.update_workflow_phase("<short-name>/synthesis", status="running", tool="synthesizing")
    synth_backend = KuzuBackend(kuzu_path)
    synth_toolkit = ReverseEngineerToolkit(synth_backend, repo_path=repo_path)
    try:
        synthesis_result = run_loop(
            provider=provider, toolkit=synth_toolkit,
            system_prompt=_build_synthesis_prompt(artifacts_dir, repo_name),
            user_request="Write the final report now.", output_root=artifacts_dir,
            max_turns=synthesis_turns, verbose=verbose,
            use_anthropic_format=use_anthropic_format,
            max_context_tokens=max_context_tokens, max_source_reads=0,
            include_write_artifact=True, phase_label="<short-name>/synthesis",
            allowed_artifact_paths={_REPORT_ARTIFACT},
        )
    except Exception:
        _log.update_workflow_phase("<short-name>/synthesis", status="failed", tool="error")
        _log.stop_agent_boxes()
        raise
    events.extend(synthesis_result["events"])
    input_tokens += synthesis_result["input_tokens"]
    output_tokens += synthesis_result["output_tokens"]
    tool_uses += synthesis_result["tool_uses"]

    state.events = list(state.events) + events
    state.input_tokens, state.output_tokens, state.tool_uses = input_tokens, output_tokens, tool_uses

    if synthesis_result["status"] == "failed":
        _log.update_workflow_phase("<short-name>/synthesis", status="failed", tool="error")
        _log.stop_agent_boxes()
        state.status, state.error = "failed", synthesis_result["error"]
        return state

    _log.update_workflow_phase("<short-name>/synthesis", status="done", tool="report written")
    _log.print_synthesizer_done(len(synthesis_result["artifacts"]))
    _log.stop_agent_boxes()

    all_artifacts.extend(synthesis_result["artifacts"])
    if not all_artifacts:
        state.status, state.error = "failed", "<name> agent completed but wrote no artifacts"
        return state

    state.artifacts_dir = artifacts_dir
    state.log("agent", f"{len(all_artifacts)} artifact(s) → {artifacts_dir}")
    return state
```

### 4. Write the pipeline module: `pipelines/<name_snake>.py`

Skeleton (copy `pipelines/security_audit.py` and rename):

```python
"""<Name> Lumen pipeline orchestration."""

from __future__ import annotations

import time
from pathlib import Path

from codedoc.preflight import run_preflights
from codedoc.pipelines.common import (
    apply_repo_size_runtime_defaults,
    create_run_dir,
    finalize_state,
    init_state,
    log_pipeline_start,
    should_stop_for_xlarge_repo,
)
from codedoc.stages.indexer import run_indexer
from codedoc.stages.<name_snake>_agent import run_agent
from codedoc.state import PipelineState


def run_pipeline(
    repo_path: str, output_dir: str, model: str, provider: str, base_url: str,
    max_turns: int, max_context_tokens: int = 120_000, ollama_num_ctx: int = 131_072,
    timeout: int = 300, repo_size_check: str = "warn", allow_xlarge: bool = False,
    timeout_explicit: bool = False, max_turns_explicit: bool = False,
    verbose: bool = False, indexer_bin_dir: str = "", repo_name: str | None = None,
) -> PipelineState:
    repo = Path(repo_path).resolve()
    resolved_repo_name = repo_name or repo.name
    run_dir = create_run_dir(output_dir, resolved_repo_name)

    state = init_state(
        repo_path=str(repo), run_dir=run_dir, mode="<name>",
        model=model, provider=provider, base_url=base_url, max_turns=max_turns,
        max_context_tokens=max_context_tokens, ollama_num_ctx=ollama_num_ctx,
        timeout=timeout, repo_size_check=repo_size_check, allow_xlarge=allow_xlarge,
        verbose=verbose, timeout_explicit=timeout_explicit,
        max_turns_explicit=max_turns_explicit, indexer_bin_dir=indexer_bin_dir,
        repo_name=resolved_repo_name,
    )
    log_pipeline_start(state, repo_path=str(repo), run_dir=run_dir, label="<name> pipeline")

    from codedoc import log as _log

    pipeline_t0 = time.time()
    try:
        state.log("pipeline", "=== Preflight: Repo Metrics ===")
        state = run_preflights(state)
        if state.status == "failed":
            return state
        state = apply_repo_size_runtime_defaults(state, bump_max_turns=True)
        if should_stop_for_xlarge_repo(state):
            state.status = "stopped"
            state.error = "Repo classified as xlarge; rerun with --allow-xlarge to continue."
            return state

        _log.print_stage_header(1, "Indexer")
        t0 = time.time()
        state = run_indexer(state)
        _log.print_stage_done(1, "Indexer", time.time() - t0)
        if state.status == "failed":
            return state

        _log.print_stage_header(2, "Agent")
        t0 = time.time()
        state = run_agent(state)
        _log.print_stage_done(2, "Agent", time.time() - t0)
        if state.status == "failed":
            return state

        state.status = "done"
        state.log("pipeline", f"Pipeline completed successfully in {time.time() - pipeline_t0:.1f}s")
    except Exception as exc:
        state.status, state.error = "failed", str(exc)
    finally:
        finalize_state(state, run_dir)

    return state
```

Skip the Stage 3 builder call entirely unless your pipeline needs an MkDocs site — see
"What NOT to touch" above.

### 5. Wire the CLI command in `cli.py`

- Import: `from codedoc.pipelines.<name_snake> import run_pipeline as run_<name_snake>_pipeline`
- Copy the `security_audit` command function (`cli.py`), rename it, keep
  `@main.command(name="<name>")` + `@common_pipeline_options` + the `repo_path` argument.
  The body (config loading, calling `run_<name_snake>_pipeline`, success/failure panel) is
  identical boilerplate — only the pipeline import and docstring change.

### 6. Make/Docker/native parity

- **Makefile**: add `lumen-<name>` (native) and `lumen-docker-<name>` (Docker) targets by
  copying the `lumen-security-audit` / `lumen-docker-security-audit` blocks and renaming.
  Add both names to `.PHONY`. Add a `CMD=lumen-<name>` / `CMD=lumen-docker-<name>` case to
  the `lumen-help` case statement, and one line each to the default (no-`CMD`) listing.
- **Docker wrapper script**: copy `scripts/lumen-docker-security-audit.sh` to
  `scripts/lumen-docker-<name>.sh`, change the subcommand in the `cmd=(...)` line from
  `security-audit` to `<name>`, `chmod +x` it.
- **Docker image, native bundle, `install-lumen.sh`**: no changes. `Dockerfile`'s
  `ENTRYPOINT ["lumen"]` and the native bundle's `lumen` launcher (`exec ... "$@"`) both
  already resolve any registered subcommand — they only need the image/bundle to contain
  the updated `codedoc` package.

### 7. Update docs

- `README.md`: add a `lumen <name> REPO_PATH [OPTIONS]` block to the CLI Reference section
  (copy the `security-audit` block), and a short usage mention if this is a second/third
  alternative pipeline worth calling out in prose.
- `CLAUDE.md`: add the new files to the `pipeline/` key-files list, and add a Design
  Decisions row if you made any non-obvious choice (e.g. skipping the builder step, a
  different role count, a different orientation query set).

### 8. Verify

Once step 6 (Makefile target) is done, `./e2e-test/test-lumen.sh` discovers your
new pipeline automatically — it greps the Makefile for `lumen-<name>:` targets and runs
each one against the checked-in fixture repos under `e2e-test/fixtures/`, verifying exit
code, `pipeline.json` status/mode, and that artifacts were actually written (see the
script itself for exactly what it checks). Run it after finishing the implementation; it
covers most of the manual checks below in one command. The manual checks are for when
`test-lumen.sh` fails and you need to narrow down why, or before the Makefile target
exists yet.

1. `python3 -c "import ast; ast.parse(open('<file>').read())"` on every new file, or just
   import them directly: `uv run python -c "import codedoc.pipelines.<name_snake>"`.
2. `uv run lumen --help` shows the new command; `uv run lumen <name> --help` shows the same
   option set as `run` (proves `common_pipeline_options` was applied correctly).
3. Smoke test against a tiny synthetic repo (3–5 files is enough — don't burn tokens/time on
   a real large repo for this). Use `--max-turns 20-30 --verbose` to bound cost and confirm:
   - Stage 1 (indexer) log output is identical in shape to `lumen run`'s.
   - The fan-out roles' log lines interleave (proves they ran concurrently, not
     sequentially) — look for alternating `[<role-a>]`/`[<role-b>]` tags.
   - Each role writes exactly its assigned artifact; the synthesis step's log shows it
     reading both role artifacts and writing the final report.
   - `pipeline.json` in the run's output dir has `"mode": "<name>"` and `"status": "done"`.
   - The "researcher done" checkmark lines, the "Tool usage by researcher" table, and the
     "synthesizer done" line all appear (these come from `print_researcher_done`,
     `print_tool_usage_table`, `print_synthesizer_done` — if they're missing, you skipped
     the `codedoc.log` wiring in step 3). In a real terminal (not piped/redirected), you
     should also see the live colored dashboard boxes update in place during Phase 2/3,
     matching `lumen run`'s look — `console.is_terminal` is `False` when output is piped,
     so redirecting to a file/`tail` will only show the plain-line fallbacks, not the boxes.
4. If you touched `pipelines/common.py` or `codedoc/log.py` (you shouldn't normally need
   to), re-verify `lumen run` still works — at minimum re-import `codedoc.pipelines.full`,
   `codedoc.pipelines.mcp`, `codedoc.pipelines.mcp_http`, and confirm
   `codedoc.log.start_agent_boxes()` (no args) still produces the exact same
   `analyst/domain|flows|tech` + `synthesis/architect/summary` state as before.
5. Clean up smoke-test output directories before committing.

---

## Your task

> Fill in this section with what you want the new pipeline to do, then hand this whole
> document + this section to whoever (or whatever) is implementing it.

- **Pipeline name** (kebab-case): `<fill in>`
- **What should it analyze or produce?** `<fill in — e.g. "test coverage gaps: which
  public methods have no corresponding test, grounded in call-graph reachability from test
  files">`
- **Fan-out roles** (2–4, each with one findings artifact): `<fill in>`
- **Fan-in synthesis output**: `<fill in — the one final report artifact>`
- **Does it need a Stage 3 (MkDocs build)?**: `<fill in — default: no>`
- **Graph tools each role is expected to lean on** (check `kg_tools/toolkit.py` for the
  full list; don't invent tool names): `<fill in>`

Implement it by following every step above, in order, using the naming conventions table
exactly. Do not modify `stages/agent.py`, `archetype_registry.py`, `artifact_planner.py`,
`preflight/*`, `stages/indexer.py`, `Dockerfile`, or `scripts/build-native.sh`.
