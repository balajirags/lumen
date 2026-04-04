from __future__ import annotations

from pathlib import Path

from codedoc.stages.indexer import detect_languages


def test_detect_languages_mixed_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("class App {}")
    (tmp_path / "src" / "widget.tsx").write_text("export const Widget = () => null;")
    (tmp_path / "src" / "worker.py").write_text("def run():\n    return 1\n")

    detected = detect_languages(str(tmp_path))

    assert detected == {"java": 1, "js": 1, "python": 1}


def test_detect_languages_ignores_generated_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignore.js").write_text("console.log('x')")
    (tmp_path / "service").mkdir()
    (tmp_path / "service" / "app.py").write_text("print('ok')")

    detected = detect_languages(str(tmp_path))

    assert detected == {"python": 1}
