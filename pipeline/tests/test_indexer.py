from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codedoc.state import PipelineState
from codedoc.stages import indexer
from codedoc.stages.indexer import detect_languages, treesitter_languages, run_indexer


@pytest.mark.parametrize(
    "ordered, flavors, expected",
    [
        (["jvm"], ["java"], ["java"]),
        (["jvm"], ["kotlin"], ["kotlin"]),
        (["jvm"], ["java", "kotlin"], ["java", "kotlin"]),
        (["jvm"], [], ["java"]),
        (["python"], [], ["python"]),
        (["php"], [], ["php"]),
        (["js"], [], ["js"]),
        (["js", "jvm", "python"], ["java"], ["js", "java", "python"]),
    ],
)
def test_treesitter_languages(ordered, flavors, expected):
    assert treesitter_languages(ordered, flavors) == expected


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "App.java").write_text("class App {}")
    (repo / "web").mkdir()
    (repo / "web" / "widget.tsx").write_text("export const Widget = () => null;")
    return repo


def _fake_run(calls: list[list[str]]):
    def run(cmd, **kwargs):
        calls.append(cmd)
        db_dir = Path(cmd[cmd.index("--db-path") + 1])
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / f"{cmd[cmd.index('--repo-name') + 1]}-db").write_bytes(b"x" * 2048)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return run


def test_run_indexer_with_flag_makes_one_cmg_ts_call_for_all_languages(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    ts_bin = tmp_path / "ts-bin"
    ts_bin.mkdir()
    (ts_bin / "cmg-ts").write_text("#!/bin/sh\n")
    calls: list[list[str]] = []
    monkeypatch.setattr(indexer.subprocess, "run", _fake_run(calls))

    state = PipelineState(
        repo_path=str(repo), output_dir=str(tmp_path / "out"), repo_name="repo",
        experimental_treesitter=True, treesitter_indexer_bin_dir=str(ts_bin),
    )
    run_indexer(state)

    assert len(calls) == 1
    assert Path(calls[0][0]).name == "cmg-ts"
    langs = calls[0][calls[0].index("--languages") + 1].split(",")
    assert sorted(langs) == ["java", "js"]
    assert state.graph_backend == "ladybug"


def test_run_indexer_without_flag_runs_one_legacy_binary_per_language(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    legacy_bin = tmp_path / "bin"
    legacy_bin.mkdir()
    for name in ("cmg-java", "cmg-js"):
        (legacy_bin / name).write_text("#!/bin/sh\n")
    calls: list[list[str]] = []
    monkeypatch.setattr(indexer.subprocess, "run", _fake_run(calls))

    state = PipelineState(
        repo_path=str(repo), output_dir=str(tmp_path / "out"), repo_name="repo",
        indexer_bin_dir=str(legacy_bin),
    )
    run_indexer(state)

    assert sorted(Path(c[0]).name for c in calls) == ["cmg-java", "cmg-js"]
    assert state.graph_backend != "ladybug"


def test_run_indexer_with_flag_errors_when_cmg_ts_missing(tmp_path):
    repo = _repo(tmp_path)
    state = PipelineState(
        repo_path=str(repo), output_dir=str(tmp_path / "out"), repo_name="repo",
        experimental_treesitter=True, treesitter_indexer_bin_dir=str(tmp_path / "missing"),
    )
    with pytest.raises(FileNotFoundError, match="treesitter-indexer/install.sh"):
        run_indexer(state)


def test_detect_languages_mixed_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("class App {}")
    (tmp_path / "src" / "widget.tsx").write_text("export const Widget = () => null;")
    (tmp_path / "src" / "worker.py").write_text("def run():\n    return 1\n")

    detected = detect_languages(str(tmp_path))

    assert detected == {"jvm": 1, "js": 1, "python": 1}


def test_detect_languages_ignores_generated_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignore.js").write_text("console.log('x')")
    (tmp_path / "service").mkdir()
    (tmp_path / "service" / "app.py").write_text("print('ok')")

    detected = detect_languages(str(tmp_path))

    assert detected == {"python": 1}
