#!/usr/bin/env python3
"""
CLI entrypoint for the tree-sitter Kotlin indexer prototype (`cmg-kotlin-ts`).
A byte-for-byte mirror of parse_php.py's CLI shape.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kotlin.extractor import extract_repo  # noqa: E402
import graph_store  # noqa: E402
from postprocess import workflow_builder, domain_detector  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="tree-sitter Kotlin indexer prototype")
    ap.add_argument("repo_path")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--repo-name", required=True)
    ap.add_argument("--backend", choices=["json", "kuzu"], default="kuzu")
    ap.add_argument("--clear", action="store_true", default=False)
    ap.add_argument("--json-out", default=None, help="only used with --backend json")
    args = ap.parse_args()

    print(f"tree-sitter Kotlin indexer: scanning {args.repo_path}", file=sys.stderr)
    graph = extract_repo(args.repo_path)
    graph_json = graph.to_json()
    print(
        f"tree-sitter Kotlin indexer: {len(graph_json['nodes'])} nodes, "
        f"{len(graph_json['relationships'])} relationships",
        file=sys.stderr,
    )

    if args.backend == "json":
        out = args.json_out or sys.stdout
        if out is sys.stdout:
            json.dump(graph_json, sys.stdout)
        else:
            with open(out, "w") as fh:
                json.dump(graph_json, fh)
        return 0

    db_path = os.path.join(os.path.abspath(args.db_path), f"{args.repo_name}-db")
    store = graph_store.LadybugGraphStore(db_path)
    if args.clear:
        store.clear()
    else:
        store.init_schema()
    store.save(graph_json, language="kotlin")

    print("tree-sitter Kotlin indexer: running post-processing (WorkflowBuilder, DomainDetector)",
          file=sys.stderr)
    workflow_builder.build_kotlin_workflows(store)
    domain_detector.detect_domains(store)

    store.close()
    print(f"tree-sitter Kotlin indexer: wrote graph to {db_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
