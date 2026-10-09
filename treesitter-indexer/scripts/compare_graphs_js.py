#!/usr/bin/env python3
"""
Validation harness for the JS/TS/React increment: runs the existing
cmg-js indexer and the new tree-sitter prototype (cmg-js-ts) against the
same fixture, diffing their output. Sibling to compare_graphs_php.py --
same asymmetric-diff philosophy (see that file's docstring). Runs against
BOTH new fixtures (mini-express-service and mini-react-app), since no
single fixture exercises both the backend-routing and React-component
gaps this increment closes.

Usage:
    python3 compare_graphs_js.py [--old-bin PATH] [--new-bin PATH]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLD_BIN = REPO_ROOT / "indexer" / "bin" / "cmg-js"
DEFAULT_NEW_BIN = REPO_ROOT / "treesitter-indexer" / "bin" / "cmg-js-ts"
DUMP_SCRIPT = Path(__file__).resolve().parent / "_dump_graph.py"

FIXTURES = [
    REPO_ROOT / "e2e-test" / "fixtures" / "mini-express-service",
    REPO_ROOT / "e2e-test" / "fixtures" / "mini-react-app",
]

NODE_TABLES = ["Class", "Function", "ArrowFunction", "Component", "Hook"]
REL_TABLES = ["CONTAINS", "CALLS", "HAS_ANNOTATION", "USES_HOOK"]

# Scoped to mini-express-service only -- mini-react-app has no HTTP routes
# at all (it's a pure React component/hook fixture), so Workflow/
# WORKFLOW_STEP are correctly zero on BOTH engines there; checking them
# globally would falsely flag that as a failure (same per-fixture-scoping
# issue already fixed for EXPECTED_NEW_ONLY_CALLS_BY_FIXTURE below).
EXPECTED_NEW_ONLY_NODE_COUNTS_BY_FIXTURE = {
    "mini-express-service": {
        "count_node::Workflow": "Workflow nodes (old engine has no backend-routing strategy for JS at all)",
    },
}
EXPECTED_NEW_ONLY_REL_COUNTS_BY_FIXTURE = {
    "mini-express-service": {
        "count_rel::WORKFLOW_STEP": "WORKFLOW_STEP (old engine never builds a workflow trace for JS routes)",
    },
}

# Caller/callee suffixes match qualifiedName -- scoped per fixture, since
# each entry only ever exists in ONE of the two fixtures (a global check
# would false-fail on whichever fixture doesn't contain a given pair).
# "OrderRepository.findOrder" (not just "findOrder") avoids ambiguously
# matching the fixture's other, unrelated findOrderRecord() free function.
EXPECTED_NEW_ONLY_CALLS_BY_FIXTURE = {
    "mini-react-app": [("handleClick", "increment", 0.9)],  # the this.method() fix, proven on the React fixture
    "mini-express-service": [("OrderRepository.findOrder", "Order.constructor", 0.9)],
}


def build_queries(fixture_name: str) -> dict[str, str]:
    queries: dict[str, str] = {}
    for table in NODE_TABLES + ["Workflow", "Domain"]:
        queries[f"count_node::{table}"] = f"MATCH (n:{table}) RETURN count(n) AS c"
    for rel in REL_TABLES + ["WORKFLOW_STEP"]:
        queries[f"count_rel::{rel}"] = f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c"
    for caller_suffix, callee_suffix, min_confidence in EXPECTED_NEW_ONLY_CALLS_BY_FIXTURE.get(fixture_name, []):
        queries[f"calls::{caller_suffix}->{callee_suffix}"] = (
            f"MATCH (a)-[c:CALLS]->(b) WHERE c.confidence >= {min_confidence} "
            f"AND a.qualifiedName CONTAINS '{caller_suffix}' AND b.qualifiedName CONTAINS '{callee_suffix}' "
            "RETURN count(c) AS c"
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
    for rel in REL_TABLES:
        old_c = scalar_count(old[f"count_rel::{rel}"])
        new_c = scalar_count(new[f"count_rel::{rel}"])
        print(f"  {rel:15s} old={old_c!s:>4} new={new_c!s:>4}")


def check_expected_new_only(old: dict, new: dict, failures: list[str], fixture_name: str) -> None:
    print("\n=== Expected new-only gaps (old engine cannot produce these at all) ===")
    node_counts = EXPECTED_NEW_ONLY_NODE_COUNTS_BY_FIXTURE.get(fixture_name, {})
    rel_counts = EXPECTED_NEW_ONLY_REL_COUNTS_BY_FIXTURE.get(fixture_name, {})
    for key, label in {**node_counts, **rel_counts}.items():
        old_c = scalar_count(old[key]) or 0
        new_c = scalar_count(new[key]) or 0
        ok = new_c > 0
        print(f"  {'OK ' if ok else 'FAIL'} {label}: old={old_c} new={new_c}")
        if not ok:
            failures.append(f"[{fixture_name}] expected new-only gap not closed: {label} (new={new_c})")

    print("\n=== Expected new-only CALLS edges ===")
    for caller_suffix, callee_suffix, _min_confidence in EXPECTED_NEW_ONLY_CALLS_BY_FIXTURE.get(fixture_name, []):
        key = f"calls::{caller_suffix}->{callee_suffix}"
        old_c = scalar_count(old.get(key)) or 0
        new_c = scalar_count(new.get(key)) or 0
        ok = new_c > 0
        print(f"  {'OK ' if ok else 'FAIL'} {caller_suffix} -> {callee_suffix}  old={old_c} new={new_c}")
        if not ok:
            failures.append(f"[{fixture_name}] expected new-only CALLS edge missing: {caller_suffix} -> {callee_suffix} (new={new_c})")


def run_one_fixture(fixture_path: Path, old_bin: Path, new_bin: Path) -> tuple[dict, dict]:
    with tempfile.TemporaryDirectory(prefix="compare-graphs-js-") as tmp:
        repo_name = fixture_path.name
        old_db_dir = Path(tmp) / "old"
        new_db_dir = Path(tmp) / "new"
        run_indexer(old_bin, fixture_path, old_db_dir, repo_name, [])
        run_indexer(new_bin, fixture_path, new_db_dir, repo_name, ["--clear"])

        queries = build_queries(fixture_path.name)
        old = dump_graph("kuzu", old_db_dir / f"{repo_name}-db", queries)
        new = dump_graph("ladybug", new_db_dir / f"{repo_name}-db", queries)
        return old, new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-bin", default=str(DEFAULT_OLD_BIN))
    ap.add_argument("--new-bin", default=str(DEFAULT_NEW_BIN))
    args = ap.parse_args()

    old_bin = Path(args.old_bin)
    new_bin = Path(args.new_bin)

    failures: list[str] = []
    for fixture_path in FIXTURES:
        print(f"\n########## Fixture: {fixture_path.name} ##########")
        old, new = run_one_fixture(fixture_path, old_bin, new_bin)
        check_comparable_counts(old, new, failures)
        check_expected_new_only(old, new, failures, fixture_path.name)

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
