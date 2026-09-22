#!/usr/bin/env python3
"""
CLI entrypoint for the tree-sitter Python indexer prototype (`cmg-python-ts`).

A byte-for-byte mirror of parse.py's CLI shape (same flags, same calling
convention: `<repo-path> --db-path <dir> --repo-name <name> [--clear]`), with
the language-specific pieces swapped: python.extractor instead of
java.extractor, workflow_builder.build_python_workflows instead of
build_jvm_workflows, and store.save(..., language="python") instead of the
default "java". Deliberately a SEPARATE script rather than a --language flag
on parse.py, matching this codebase's existing "byte-for-byte mirror, swap
the one thing that differs" pattern (see lumen-docker-security-audit.sh).

Same --clear opt-in-default rationale as parse.py: this is new, less-trusted
code, so the destructive-clear default is inverted from App.java's.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from python.extractor import extract_repo  # noqa: E402
from config.extractor import extract_config  # noqa: E402
import graph_store  # noqa: E402
from postprocess import workflow_builder, domain_detector  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="tree-sitter Python indexer prototype")
    ap.add_argument("repo_path")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--repo-name", required=True)
    ap.add_argument("--backend", choices=["json", "kuzu"], default="kuzu")
    ap.add_argument("--clear", action="store_true", default=False)
    ap.add_argument("--no-config", action="store_true", default=False,
                     help="skip YAML/JSON/.properties config extraction")
    ap.add_argument("--json-out", default=None, help="only used with --backend json")
    args = ap.parse_args()

    print(f"tree-sitter Python indexer: scanning {args.repo_path}", file=sys.stderr)
    graph = extract_repo(args.repo_path)
    if not args.no_config:
        # Python repos have no src/main/resources convention -- extract_config
        # is Spring-resources-root-specific (see repo_layout.detect_resources_root)
        # and will simply find nothing to scan for a typical Python repo layout;
        # kept here only so a Python repo that happens to embed a Java-style
        # resources tree (e.g. a polyglot monorepo slice) still gets it for free.
        extract_config(args.repo_path, graph)
    graph_json = graph.to_json()
    print(
        f"tree-sitter Python indexer: {len(graph_json['nodes'])} nodes, "
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
    store.save(graph_json, language="python")

    print("tree-sitter Python indexer: running post-processing (WorkflowBuilder, DomainDetector)",
          file=sys.stderr)
    workflow_builder.build_python_workflows(store)
    domain_detector.detect_domains(store)

    store.close()
    print(f"tree-sitter Python indexer: wrote graph to {db_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
