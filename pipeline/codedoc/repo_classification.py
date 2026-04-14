from __future__ import annotations

import json
import re
from pathlib import Path

from codedoc.archetype_registry import resolve_archetype
from codedoc.artifact_planner import infer_capabilities
from codedoc.language_registry import LANGUAGE_CATEGORIES, category_for_suffix


IGNORED_PARTS = {"node_modules", "__pycache__", "venv", ".venv"}
# These directories are only ignored at the repo root (depth 0) — they can
# legitimately appear as Java/Kotlin package names deeper in the tree.
IGNORED_ROOT_DIRS = {"build", "dist", "target"}

FRONTEND_DEP_MARKERS = {
    "@angular/core",
    "@remix-run/react",
    "@tanstack/react-query",
    "next",
    "nuxt",
    "preact",
    "react",
    "react-dom",
    "solid-js",
    "svelte",
    "vue",
}

BACKEND_DEP_MARKERS = {
    "@nestjs/core",
    "apollo-server",
    "express",
    "fastapi",
    "fastify",
    "flask",
    "hapi",
    "koa",
}

FRONTEND_PATH_MARKERS = {"app", "components", "frontend", "hooks", "pages", "screens", "ui", "views", "web"}
BACKEND_PATH_MARKERS = {"backend", "controller", "controllers", "handler", "handlers", "server", "servers", "middleware"}
LIBRARY_PATH_MARKERS = {"lib", "libs", "sdk", "shared"}

JS_HTTP_API_PATTERNS = (
    re.compile(r"\bexpress\s*\("),
    re.compile(r"\bexpress\.Router\s*\("),
    re.compile(r"\brouter\.(get|post|put|delete|patch|use)\s*\("),
    re.compile(r"\bapp\.(get|post|put|delete|patch|use|listen)\s*\("),
    re.compile(r"\bfastify\s*\("),
    re.compile(r"\bkoa\s*\("),
    re.compile(r"\bcreateServer\s*\("),
    re.compile(r"\bhttp\.createServer\s*\("),
    re.compile(r"@\s*Controller\b"),
    re.compile(r"@\s*(Get|Post|Put|Delete|Patch)\b"),
)

JS_API_ROUTE_PATH_PATTERNS = (
    re.compile(r"(^|/)pages/api/"),
    re.compile(r"(^|/)app/api/"),
    re.compile(r"(^|/)src/pages/api/"),
    re.compile(r"(^|/)src/app/api/"),
)


def is_ignored_path(path: Path) -> bool:
    parts = path.parts
    if not parts:
        return False
    # Always-ignored directories at any depth
    if any(part.startswith(".") or part in IGNORED_PARTS for part in parts):
        return True
    # Build output directories ignored only at repo root (first component)
    if parts[0] in IGNORED_ROOT_DIRS:
        return True
    return False


def _iter_source_files(repo: Path):
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if is_ignored_path(rel.parent):
            continue
        definition = category_for_suffix(path.suffix)
        if definition:
            yield path, rel, definition


def _scan_package_json(repo: Path) -> tuple[int, int, int]:
    package_json = repo / "package.json"
    if not package_json.exists():
        return 0, 0, 0
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0, 0

    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key, {})
        if isinstance(section, dict):
            deps.update(str(name) for name in section.keys())

    frontend = 2 if deps & FRONTEND_DEP_MARKERS else 0
    backend = 2 if deps & BACKEND_DEP_MARKERS else 0
    library = 1 if any(key in data for key in ("exports", "main", "module", "types")) else 0
    return frontend, backend, library


def _scan_repo_markers(repo: Path) -> tuple[int, int, int]:
    frontend = 0
    backend = 0
    library = 0

    frontend_pkg, backend_pkg, library_pkg = _scan_package_json(repo)
    frontend += frontend_pkg
    backend += backend_pkg
    library += library_pkg

    if (repo / "pom.xml").exists() or (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
        backend += 1
    if (repo / "manage.py").exists():
        backend += 1
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        library += 1
    if any((repo / fname).exists() for fname in ("vite.config.js", "vite.config.ts", "next.config.js", "next.config.mjs")):
        frontend += 1

    return frontend, backend, library


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _js_backend_api_signal(rel: Path, stem: str, content: str) -> int:
    rel_str = rel.as_posix().lower()
    score = 0

    if any(pattern.search(rel_str) for pattern in JS_API_ROUTE_PATH_PATTERNS):
        score += 3

    if stem in {"server", "controller", "handler"}:
        score += 1

    if any(pattern.search(content) for pattern in JS_HTTP_API_PATTERNS):
        score += 3

    if "listen(" in content and ("express" in content or "fastify" in content):
        score += 1

    return score


def classify_repo(repo_path: str) -> dict[str, object]:
    repo = Path(repo_path)

    files_by_category = {definition.key: 0 for definition in LANGUAGE_CATEGORIES}
    loc_by_category = {definition.key: 0 for definition in LANGUAGE_CATEGORIES}
    flavor_counts: dict[str, int] = {}
    signal_counts = {"frontend-ui": 0, "backend-api": 0, "library": 0}

    marker_frontend, marker_backend, marker_library = _scan_repo_markers(repo)
    signal_counts["frontend-ui"] += marker_frontend
    signal_counts["backend-api"] += marker_backend
    signal_counts["library"] += marker_library

    for path, rel, definition in _iter_source_files(repo):
        files_by_category[definition.key] += 1
        for flavor in definition.flavors:
            if path.suffix.lower() == ".java" and flavor != "java":
                continue
            if path.suffix.lower() in {".kt", ".kts"} and flavor != "kotlin":
                continue
            if definition.key == "js" and flavor != "js":
                continue
            if definition.key == "python" and flavor != "python":
                continue
            flavor_counts[flavor] = flavor_counts.get(flavor, 0) + 1
        content = _read_text(path)
        if not content:
            continue
        loc_by_category[definition.key] += sum(1 for line in content.splitlines() if line.strip())

        rel_parts = {part.lower() for part in rel.parts}
        stem = path.stem.lower()
        suffix = path.suffix.lower()

        if definition.key == "js":
            if suffix in {".jsx", ".tsx"} or rel_parts & FRONTEND_PATH_MARKERS or stem in {"app", "layout", "page"}:
                signal_counts["frontend-ui"] += 1
            signal_counts["backend-api"] += _js_backend_api_signal(rel, stem, content)
        elif definition.key == "python":
            if rel_parts & BACKEND_PATH_MARKERS or stem in {"app", "asgi", "wsgi", "manage"}:
                signal_counts["backend-api"] += 1
        elif definition.key == "jvm":
            if rel_parts & BACKEND_PATH_MARKERS or "src" in rel_parts:
                signal_counts["backend-api"] += 1

        if rel_parts & LIBRARY_PATH_MARKERS:
            signal_counts["library"] += 1

    detected_language_categories = [
        definition.key for definition in LANGUAGE_CATEGORIES if files_by_category[definition.key] > 0
    ]
    detected_flavors = [name for name in ("java", "kotlin", "js", "python") if flavor_counts.get(name, 0) > 0]
    selected_archetype = resolve_archetype(signal_counts, detected_language_categories)
    capabilities = infer_capabilities(selected_archetype, {
        "detected_language_categories": detected_language_categories,
        "archetype_signals": [name for name in ("frontend-ui", "backend-api", "library") if signal_counts[name] > 0],
        "archetype_signal_counts": signal_counts,
    })
    archetype_signals = [name for name in ("frontend-ui", "backend-api", "library") if signal_counts[name] > 0]

    compat_files_by_language = {}
    compat_loc_by_language = {}
    compat_detected_languages: list[str] = []
    for definition in LANGUAGE_CATEGORIES:
        if files_by_category[definition.key] <= 0:
            continue
        compat_files_by_language[definition.compat_alias] = files_by_category[definition.key]
        compat_loc_by_language[definition.compat_alias] = loc_by_category[definition.key]
        compat_detected_languages.append(definition.compat_alias)

    return {
        "total_loc": sum(loc_by_category.values()),
        "total_source_files": sum(files_by_category.values()),
        "files_by_category": {k: v for k, v in files_by_category.items() if v > 0},
        "loc_by_category": {k: v for k, v in loc_by_category.items() if v > 0},
        "language_flavors": detected_flavors,
        "files_by_flavor": {k: v for k, v in flavor_counts.items() if v > 0},
        "detected_language_categories": detected_language_categories,
        "archetype_signals": archetype_signals,
        "archetype_signal_counts": {k: v for k, v in signal_counts.items() if v > 0},
        "selected_archetype": selected_archetype,
        "primary_repo_type": selected_archetype,
        "capabilities": capabilities,
        "mixed_archetype": selected_archetype == "fullstack-app",
        "files_by_language": compat_files_by_language,
        "loc_by_language": compat_loc_by_language,
        "detected_languages": compat_detected_languages,
    }
