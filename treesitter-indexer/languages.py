"""
Single registry of the languages the tree-sitter engine supports. parse_all.py
iterates this instead of hardcoding per-language branches, so adding a
language is one entry here (plus its extractor/builder), not a new code path.

Imports are lazy (inside the lambdas): each language pulls in its own
tree-sitter grammar, and a run for a Python-only repo shouldn't pay to import
the Kotlin/PHP/JS grammars.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Language:
    key: str
    extract_repo: Callable[[str], object]
    build_workflows: Callable[[object], int]
    uses_config: bool = False


def _java_extract(repo_path):
    from java.extractor import extract_repo
    return extract_repo(repo_path)


def _python_extract(repo_path):
    from python.extractor import extract_repo
    return extract_repo(repo_path)


def _php_extract(repo_path):
    from php.extractor import extract_repo
    return extract_repo(repo_path)


def _js_extract(repo_path):
    from js.extractor import extract_repo
    return extract_repo(repo_path)


def _kotlin_extract(repo_path):
    from kotlin.extractor import extract_repo
    return extract_repo(repo_path)


def _builder(name: str) -> Callable[[object], int]:
    def run(store):
        from postprocess import workflow_builder
        return getattr(workflow_builder, name)(store)
    return run


LANGUAGES: dict[str, Language] = {
    lang.key: lang for lang in (
        Language("java", _java_extract, _builder("build_jvm_workflows"), uses_config=True),
        Language("python", _python_extract, _builder("build_python_workflows"), uses_config=True),
        Language("php", _php_extract, _builder("build_php_workflows")),
        Language("js", _js_extract, _builder("build_js_workflows")),
        Language("kotlin", _kotlin_extract, _builder("build_kotlin_workflows")),
    )
}
