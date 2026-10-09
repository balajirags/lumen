from __future__ import annotations

from pathlib import Path

from codedoc.state import PipelineState
from codedoc.stages.indexer import (
    detect_languages,
    should_use_treesitter_java,
    should_use_treesitter_python,
    should_use_treesitter_php,
    should_use_treesitter_js,
)


def _state(**overrides) -> PipelineState:
    return PipelineState(**overrides)


def test_should_use_treesitter_java_when_flag_set_and_java_only():
    state = _state(experimental_treesitter_java=True, language_flavors=["java"])
    assert should_use_treesitter_java(state, ["jvm"]) is True


def test_should_use_treesitter_java_false_when_flag_unset():
    state = _state(experimental_treesitter_java=False, language_flavors=["java"])
    assert should_use_treesitter_java(state, ["jvm"]) is False


def test_should_use_treesitter_java_false_for_polyglot_repo():
    state = _state(experimental_treesitter_java=True, language_flavors=["java"])
    assert should_use_treesitter_java(state, ["jvm", "python"]) is False


def test_should_use_treesitter_java_false_when_kotlin_present():
    state = _state(experimental_treesitter_java=True, language_flavors=["java", "kotlin"])
    assert should_use_treesitter_java(state, ["jvm"]) is False


def test_should_use_treesitter_python_when_flag_set_and_python_only():
    state = _state(experimental_treesitter_python=True)
    assert should_use_treesitter_python(state, ["python"]) is True


def test_should_use_treesitter_python_false_when_flag_unset():
    state = _state(experimental_treesitter_python=False)
    assert should_use_treesitter_python(state, ["python"]) is False


def test_should_use_treesitter_python_false_for_polyglot_repo():
    state = _state(experimental_treesitter_python=True)
    assert should_use_treesitter_python(state, ["python", "jvm"]) is False


def test_should_use_treesitter_php_when_flag_set_and_php_only():
    state = _state(experimental_treesitter_php=True)
    assert should_use_treesitter_php(state, ["php"]) is True


def test_should_use_treesitter_php_false_when_flag_unset():
    state = _state(experimental_treesitter_php=False)
    assert should_use_treesitter_php(state, ["php"]) is False


def test_should_use_treesitter_php_false_for_polyglot_repo():
    state = _state(experimental_treesitter_php=True)
    assert should_use_treesitter_php(state, ["php", "jvm"]) is False


def test_should_use_treesitter_js_when_flag_set_and_js_only():
    state = _state(experimental_treesitter_js=True)
    assert should_use_treesitter_js(state, ["js"]) is True


def test_should_use_treesitter_js_false_when_flag_unset():
    state = _state(experimental_treesitter_js=False)
    assert should_use_treesitter_js(state, ["js"]) is False


def test_should_use_treesitter_js_false_for_polyglot_repo():
    state = _state(experimental_treesitter_js=True)
    assert should_use_treesitter_js(state, ["js", "python"]) is False


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
