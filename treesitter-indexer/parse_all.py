#!/usr/bin/env python3
"""
Multi-language CLI entrypoint for the tree-sitter indexer (`cmg-ts`).

Mirrors what App.java does for the old engine: extract every requested
language into an in-memory Graph, merge them into ONE graph, write it ONCE,
then run each language's WorkflowBuilder and a single DomainDetector pass over
the merged result. The per-language parse_*.py scripts remain as
single-language debugging entrypoints; they run the same steps for one language.

--clear is opt-in (same safe default as every parse_*.py).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graph_model import Graph  # noqa: E402
from languages import LANGUAGES  # noqa: E402


def build_merged_graph(repo_path: str, languages: list[str], include_config: bool = True) -> Graph:
    merged = Graph()
    config_done = False
    for key in languages:
        lang = LANGUAGES[key]
        print(f"tree-sitter indexer: extracting {key} from {repo_path}", file=sys.stderr)
        graph = lang.extract_repo(repo_path)
        # Java and Python both use config extraction; run it once, not per language.
        if include_config and lang.uses_config and not config_done:
            from config.extractor import extract_config
            extract_config(repo_path, graph)
            config_done = True
        graph.stamp_language(key)
        collisions = merged.merge(graph)
        if collisions:
            print(f"tree-sitter indexer: WARNING {len(collisions)} cross-language id collision(s) "
                  f"while merging {key} (first writer kept): {collisions[:5]}", file=sys.stderr)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description="tree-sitter multi-language indexer")
    ap.add_argument("repo_path")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--repo-name", required=True)
    ap.add_argument("--languages", required=True,
                    help=f"comma-separated subset of: {','.join(LANGUAGES)}")
    ap.add_argument("--clear", action="store_true", default=False)
    ap.add_argument("--no-config", action="store_true", default=False,
                    help="skip YAML/JSON/.properties config extraction")
    args = ap.parse_args()

    languages = [l.strip() for l in args.languages.split(",") if l.strip()]
    unknown = [l for l in languages if l not in LANGUAGES]
    if unknown or not languages:
        ap.error(f"unsupported --languages {unknown or '(empty)'}; choose from {','.join(LANGUAGES)}")

    import graph_store
    from postprocess import domain_detector

    graph = build_merged_graph(args.repo_path, languages, include_config=not args.no_config)
    graph_json = graph.to_json()
    print(f"tree-sitter indexer: merged {len(graph_json['nodes'])} nodes, "
          f"{len(graph_json['relationships'])} relationships across {','.join(languages)}",
          file=sys.stderr)

    db_path = os.path.join(os.path.abspath(args.db_path), f"{args.repo_name}-db")
    store = graph_store.LadybugGraphStore(db_path)
    if args.clear:
        store.clear()
    else:
        store.init_schema()
    store.save(graph_json)

    print("tree-sitter indexer: running post-processing (WorkflowBuilder per language, "
          "DomainDetector once)", file=sys.stderr)
    for key in languages:
        LANGUAGES[key].build_workflows(store)
    domain_detector.detect_domains(store)

    store.close()
    print(f"tree-sitter indexer: wrote graph to {db_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
