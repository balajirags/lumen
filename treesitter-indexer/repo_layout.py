"""
Repo layout detection shared by java/extractor.py and config/extractor.py.
Ported from indexer/app/.../App.java's detectSourceRoot/detectModules.
"""

from __future__ import annotations

import os


def detect_modules(project_root: str) -> list[str]:
    """A subdirectory counts as its own module if it has both a build file
    and a src layout of its own (mirrors App.java's detectModules)."""
    modules: list[str] = []
    try:
        for name in sorted(os.listdir(project_root)):
            child = os.path.join(project_root, name)
            if not os.path.isdir(child):
                continue
            has_own_build = any(
                os.path.exists(os.path.join(child, f)) for f in ("build.gradle", "build.gradle.kts", "pom.xml")
            )
            has_src = any(
                os.path.isdir(os.path.join(child, d)) for d in ("src/main/java", "src/main", "src")
            )
            if has_own_build and has_src:
                modules.append(child)
    except OSError:
        pass

    if not modules:
        return [project_root]

    if any(os.path.isdir(os.path.join(project_root, d)) for d in ("src/main/java", "src/main")):
        modules.insert(0, project_root)
    return modules


def detect_source_root(module_root: str) -> str:
    """Standard Maven/Gradle layout puts sources under src/main/java; TEST
    sources under src/test/java are deliberately never scanned. Falls back to
    src/main or src for non-standard layouts, then the module root itself."""
    for candidate in ("src/main/java", "src/main", "src"):
        path = os.path.join(module_root, candidate)
        if os.path.isdir(path):
            return path
    return module_root


def detect_resources_root(module_root: str) -> str | None:
    """Spring's convention puts resources (application.yml/.properties) under
    src/main/resources -- src/test/resources is deliberately never scanned.
    Unlike detect_source_root, this does NOT fall back to the module root
    itself: config files (especially JSON) are common noise everywhere in a
    repo (package.json, IDE config, ...), so scanning is skipped entirely
    rather than guessing when no resources-ish layout is present."""
    for candidate in ("src/main/resources", "src/main", "src"):
        path = os.path.join(module_root, candidate)
        if os.path.isdir(path):
            return path
    return None
