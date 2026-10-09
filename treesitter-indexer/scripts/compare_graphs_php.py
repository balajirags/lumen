#!/usr/bin/env python3
"""
Validation harness for the PHP increment: runs the existing cmg-php indexer
and the new tree-sitter prototype (cmg-php-ts) against the same fixture,
diffing their output. Sibling to compare_graphs_python.py -- same
asymmetric-diff philosophy (see that file's docstring).

Usage:
    python3 compare_graphs_php.py [fixture-repo-path] [--old-bin PATH] [--new-bin PATH]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLD_BIN = REPO_ROOT / "indexer" / "bin" / "cmg-php"
DEFAULT_NEW_BIN = REPO_ROOT / "treesitter-indexer" / "bin" / "cmg-php-ts"
DUMP_SCRIPT = Path(__file__).resolve().parent / "_dump_graph.py"

NODE_TABLES = ["Class", "Interface", "Method", "Function"]
REL_TABLES = ["CONTAINS", "CALLS", "HAS_ANNOTATION"]

EXPECTED_NEW_ONLY_NODE_COUNTS = {
    "count_node::Parameter": "Parameter nodes (old engine never reads type hints for PHP)",
}
EXPECTED_NEW_ONLY_REL_COUNTS = {
    "count_rel::HAS_PARAMETER": "HAS_PARAMETER (old engine never emits this for PHP)",
    "count_rel::RETURNS": "RETURNS (old engine never emits this for PHP)",
    "count_rel::WORKFLOW_STEP": "WORKFLOW_STEP for middleware-group-nested routes "
                                  "(old engine's RouteExtractor finds the route but not the middleware signal)",
}

# Caller/callee suffixes match qualifiedName, NOT filename -- e.g. the free
# function dispatchToChannel() lives in NotificationDispatcher.php but its
# qualifiedName is "App\Services\dispatchToChannel" (the function's own
# name), the filename never appears in it.
EXPECTED_NEW_ONLY_CALLS = [
    ("OrderRepository.find", "Order.__construct", 0.9),
    ("dispatchToChannel", "notifyOrderReady", 0.5),
]


def build_queries() -> dict[str, str]:
    queries: dict[str, str] = {}
    for table in NODE_TABLES + ["Parameter", "Workflow", "Domain"]:
        queries[f"count_node::{table}"] = f"MATCH (n:{table}) RETURN count(n) AS c"
    for rel in REL_TABLES + ["HAS_PARAMETER", "RETURNS", "WORKFLOW_STEP"]:
        queries[f"count_rel::{rel}"] = f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c"
    for caller_suffix, callee_suffix, min_confidence in EXPECTED_NEW_ONLY_CALLS:
        queries[f"calls::{caller_suffix}->{callee_suffix}"] = (
            f"MATCH (a)-[c:CALLS]->(b) WHERE c.confidence >= {min_confidence} "
            f"AND a.qualifiedName CONTAINS '{caller_suffix}' AND b.qualifiedName CONTAINS '{callee_suffix}' "
            "RETURN count(c) AS c"
        )
    queries["entry_points"] = (
        "MATCH (m:Method)-[r:HAS_ANNOTATION]->(a:AnnotationType) RETURN m.qualifiedName AS fn, a.name AS name, r.value AS value"
    )
    return queries


def dump_graph(backend: str, db_path: Path, queries: dict[str, str]) -> dict:
    proc = subprocess.run(
        [sys.executable, str(DUMP_SCRIPT), "--backend", backend, "--db-path", str(db_path)],
        input=json.dumps(queries), capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def run_indexer(binary: Path, repo_path: Path, db_dir: Path, repo_name: str, extra_args: list[str]) -> None:
    cmd = [str(binary), str(repo_path), "--db-path", str(db_dir), "--repo-name", repo_name, *extra_args]
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, check=True)


def scalar_count(rows) -> int | None:
    if isinstance(rows, dict) and "__error__" in rows:
        return None
    return rows[0]["c"] if rows else 0


def check_comparable_counts(old: dict, new: dict, failures: list[str]) -> None:
    print("\n=== Comparable node/rel table counts (old vs new, not expected to match exactly) ===")
    for table in NODE_TABLES:
        old_c = scalar_count(old[f"count_node::{table}"])
        new_c = scalar_count(new[f"count_node::{table}"])
        print(f"  {table:15s} old={old_c!s:>4} new={new_c!s:>4}")
        if (old_c or 0) > 0 and (new_c or 0) == 0:
            failures.append(f"node table {table}: old has {old_c} but new has 0")
    for rel in REL_TABLES:
        old_c = scalar_count(old[f"count_rel::{rel}"])
        new_c = scalar_count(new[f"count_rel::{rel}"])
        print(f"  {rel:15s} old={old_c!s:>4} new={new_c!s:>4}")
        if (old_c or 0) > 0 and (new_c or 0) == 0:
            failures.append(f"rel table {rel}: old has {old_c} but new has 0")


def check_expected_new_only(old: dict, new: dict, failures: list[str]) -> None:
    print("\n=== Expected new-only gaps (old engine cannot produce these at all) ===")
    for key, label in {**EXPECTED_NEW_ONLY_NODE_COUNTS, **EXPECTED_NEW_ONLY_REL_COUNTS}.items():
        old_c = scalar_count(old[key]) or 0
        new_c = scalar_count(new[key]) or 0
        ok = new_c > 0
        print(f"  {'OK ' if ok else 'FAIL'} {label}: old={old_c} new={new_c}")
        if not ok:
            failures.append(f"expected new-only gap not closed: {label} (new={new_c})")

    print("\n=== Expected new-only CALLS edges ===")
    for caller_suffix, callee_suffix, _min_confidence in EXPECTED_NEW_ONLY_CALLS:
        key = f"calls::{caller_suffix}->{callee_suffix}"
        old_c = scalar_count(old[key]) or 0
        new_c = scalar_count(new[key]) or 0
        ok = new_c > 0
        print(f"  {'OK ' if ok else 'FAIL'} {caller_suffix} -> {callee_suffix}  old={old_c} new={new_c}")
        if not ok:
            failures.append(f"expected new-only CALLS edge missing: {caller_suffix} -> {callee_suffix} (new={new_c})")


def check_workflows(new: dict, failures: list[str]) -> None:
    print("\n=== Workflows ===")
    workflow_count = scalar_count(new["count_node::Workflow"]) or 0
    print(f"  Workflow nodes: {workflow_count}")
    if workflow_count == 0:
        failures.append("new engine produced zero Workflow nodes -- expected at least one from the fixture's routes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture_path", nargs="?",
                     default=str(REPO_ROOT / "e2e-test" / "fixtures" / "mini-php-service"))
    ap.add_argument("--old-bin", default=str(DEFAULT_OLD_BIN))
    ap.add_argument("--new-bin", default=str(DEFAULT_NEW_BIN))
    args = ap.parse_args()

    fixture_path = Path(args.fixture_path).resolve()
    old_bin = Path(args.old_bin)
    new_bin = Path(args.new_bin)

    with tempfile.TemporaryDirectory(prefix="compare-graphs-php-") as tmp:
        repo_name = fixture_path.name
        old_db_dir = Path(tmp) / "old"
        new_db_dir = Path(tmp) / "new"
        run_indexer(old_bin, fixture_path, old_db_dir, repo_name, [])
        run_indexer(new_bin, fixture_path, new_db_dir, repo_name, ["--clear"])

        queries = build_queries()
        old = dump_graph("kuzu", old_db_dir / f"{repo_name}-db", queries)
        new = dump_graph("ladybug", new_db_dir / f"{repo_name}-db", queries)

        failures: list[str] = []
        check_comparable_counts(old, new, failures)
        check_expected_new_only(old, new, failures)
        check_workflows(new, failures)

        print("\n=== Result ===")
        if failures:
            print(f"FAIL ({len(failures)} issue(s)):")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
