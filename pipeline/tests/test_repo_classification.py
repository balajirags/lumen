from __future__ import annotations

import json

from codedoc.archetype_registry import archetype_definition, resolve_archetype
from codedoc.language_registry import LANGUAGE_CATEGORY_BY_KEY, category_for_suffix
from codedoc.repo_classification import classify_repo


def test_language_registry_drives_suffix_resolution():
    assert category_for_suffix(".java") == LANGUAGE_CATEGORY_BY_KEY["jvm"]
    assert category_for_suffix(".tsx") == LANGUAGE_CATEGORY_BY_KEY["js"]
    assert category_for_suffix(".pyi") == LANGUAGE_CATEGORY_BY_KEY["python"]


def test_archetype_registry_resolves_fullstack_from_signals():
    assert resolve_archetype({"frontend-ui": 3, "backend-api": 2, "library": 0}, ["js"]) == "fullstack-app"
    assert archetype_definition("fullstack-app").guidance_file == "archetype-fullstack-app.md"


def test_classify_repo_jvm_category_collapses_java_and_kotlin(tmp_path):
    (tmp_path / "src" / "main" / "java").mkdir(parents=True)
    (tmp_path / "src" / "main" / "kotlin").mkdir(parents=True)
    (tmp_path / "src" / "main" / "java" / "App.java").write_text("class App {}")
    (tmp_path / "src" / "main" / "kotlin" / "Feature.kt").write_text("class Feature")

    metrics = classify_repo(str(tmp_path))

    assert metrics["detected_language_categories"] == ["jvm"]
    assert metrics["language_flavors"] == ["java", "kotlin"]
    assert metrics["selected_archetype"] == "backend-service"


def test_classify_repo_js_fullstack_detects_both_archetype_signals(tmp_path):
    (tmp_path / "frontend" / "components").mkdir(parents=True)
    (tmp_path / "backend" / "routes").mkdir(parents=True)
    (tmp_path / "frontend" / "components" / "App.tsx").write_text("export function App(){ return null }")
    (tmp_path / "backend" / "routes" / "server.ts").write_text("export const server = {}")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "react": "^19.0.0",
                    "express": "^5.0.0",
                }
            }
        )
    )

    metrics = classify_repo(str(tmp_path))

    assert metrics["detected_language_categories"] == ["js"]
    assert metrics["selected_archetype"] == "fullstack-app"
    assert metrics["archetype_signals"] == ["frontend-ui", "backend-api"]


def test_classify_repo_js_library_defaults_to_library(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "index.ts").write_text("export const x = 1")
    (tmp_path / "package.json").write_text(json.dumps({"name": "sdk", "exports": "./lib/index.ts"}))

    metrics = classify_repo(str(tmp_path))

    assert metrics["detected_language_categories"] == ["js"]
    assert metrics["selected_archetype"] == "library"
