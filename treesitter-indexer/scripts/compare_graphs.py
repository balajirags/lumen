#!/usr/bin/env python3
"""
Validation harness: runs the existing cmg-java indexer and the new tree-sitter
prototype (cmg-java-ts) against the same fixture, into two separate throwaway
--db-path directories (never the pipeline's shared index.kuzu), and diffs
their output.

Usage:
    python3 compare_graphs.py <fixture-repo-path> [--old-bin PATH] [--new-bin PATH]

Requires `kuzu` installed in addition to `ladybug` (run `uv sync --extra dev`)
-- both are needed because each engine's graph is read via `_dump_graph.py`,
run as its own subprocess (kuzu's and ladybug's native extensions can't be
imported in the same process -- see that file's docstring).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLD_BIN = REPO_ROOT / "indexer" / "bin" / "cmg-java"
DEFAULT_NEW_BIN = REPO_ROOT / "treesitter-indexer" / "bin" / "cmg-java-ts"
DUMP_SCRIPT = Path(__file__).resolve().parent / "_dump_graph.py"

# Wave B (CPG) tables the new engine deliberately doesn't populate yet --
# excluded from pass/fail count comparison, shown as informational only.
WAVE_B_TABLES = {"Statement", "File", "AST_CHILD", "CFG_NEXT", "DATA_FLOW", "SOURCE_FILE"}

NODE_TABLES = [
    "Package", "Class", "Interface", "Enum", "Record", "AnnotationType",
    "Method", "Constructor", "Field", "Parameter", "Workflow", "Domain",
]
REL_TABLES = [
    "CONTAINS", "EXTENDS", "IMPLEMENTS", "CALLS", "RETURNS", "HAS_PARAMETER",
    "OF_TYPE", "HAS_ANNOTATION", "OVERRIDES", "THROWS", "WORKFLOW_STEP", "IN_DOMAIN",
]

EXPECTED_QUALIFIED_NAMES = [
    "com.example.inventory.controller.InventoryController",
    "com.example.inventory.service.InventoryService",
    "com.example.inventory.repository.ItemRepository",
    "com.example.inventory.notify.Notifier",
    "com.example.inventory.notify.EmailNotifier",
    "com.example.inventory.model.Item",
]

# (callerSimpleQN substring, calleeSimpleQN substring) pairs that must appear
# as a CALLS edge with confidence >= 0.9 in both graphs.
EXPECTED_CALLS = [
    ("InventoryController.getItems", "InventoryService.findAll"),
    ("InventoryController.addItem", "InventoryService.addItem"),
    ("InventoryService.findAll", "ItemRepository.findAllItems"),
    ("InventoryService.addItem", "ItemRepository.save"),
]


def build_queries() -> dict[str, str]:
    queries: dict[str, str] = {}
    for table in NODE_TABLES:
        queries[f"count_node::{table}"] = f"MATCH (n:{table}) RETURN count(n) AS c"
    for rel in REL_TABLES:
        queries[f"count_rel::{rel}"] = f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c"
    for qn in EXPECTED_QUALIFIED_NAMES:
        queries[f"identity::{qn}"] = (
            f"MATCH (n) WHERE n.qualifiedName = '{qn}' RETURN label(n) AS kind, n.normKind AS normKind"
        )
    for caller_suffix, callee_suffix in EXPECTED_CALLS:
        queries[f"calls::{caller_suffix}->{callee_suffix}"] = (
            "MATCH (a)-[c:CALLS]->(b) WHERE c.confidence >= 0.9 "
            f"AND a.qualifiedName CONTAINS '{caller_suffix}' AND b.qualifiedName CONTAINS '{callee_suffix}' "
            "RETURN count(c) AS c"
        )
    queries["workflows"] = "MATCH (w:Workflow) RETURN w.httpMethod AS method, w.httpPath AS path, w.stepCount AS steps"
    queries["domains"] = "MATCH (d:Domain) WHERE d.memberCount >= 2 RETURN d.memberCount AS members"
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


def check_counts(old: dict, new: dict, failures: list[str]) -> None:
    print("\n=== Node/rel table counts (old vs new) ===")
    for table in NODE_TABLES:
        old_c = scalar_count(old[f"count_node::{table}"])
        new_c = scalar_count(new[f"count_node::{table}"])
        print(f"  {table:15s} old={old_c!s:>4} new={new_c!s:>4}")
    for rel in REL_TABLES:
        old_c = scalar_count(old[f"count_rel::{rel}"])
        new_c = scalar_count(new[f"count_rel::{rel}"])
        note = " (Wave B, informational)" if rel in WAVE_B_TABLES else ""
        print(f"  {rel:15s} old={old_c!s:>4} new={new_c!s:>4}{note}")
        if rel not in WAVE_B_TABLES and (old_c or 0) > 0 and (new_c or 0) == 0:
            failures.append(f"rel table {rel}: old has {old_c} but new has 0")


def check_identity_spot_checks(old: dict, new: dict, failures: list[str]) -> None:
    print("\n=== Identity spot-checks ===")
    for qn in EXPECTED_QUALIFIED_NAMES:
        old_rows = old[f"identity::{qn}"]
        new_rows = new[f"identity::{qn}"]
        ok = bool(old_rows) and bool(new_rows) and old_rows[0]["kind"] == new_rows[0]["kind"]
        print(f"  {'OK ' if ok else 'FAIL'} {qn}  old={old_rows} new={new_rows}")
        if not ok:
            failures.append(f"identity spot-check failed for {qn}")


def check_calls(old: dict, new: dict, failures: list[str]) -> None:
    print("\n=== Expected CALLS edges (confidence >= 0.9) ===")
    for caller_suffix, callee_suffix in EXPECTED_CALLS:
        key = f"calls::{caller_suffix}->{callee_suffix}"
        old_c = scalar_count(old[key]) or 0
        new_c = scalar_count(new[key]) or 0
        ok = old_c > 0 and new_c > 0
        print(f"  {'OK ' if ok else 'FAIL'} {caller_suffix} -> {callee_suffix}  old={old_c} new={new_c}")
        if not ok:
            failures.append(f"CALLS edge missing: {caller_suffix} -> {callee_suffix} (old={old_c}, new={new_c})")


def check_workflows_and_domains(old: dict, new: dict, failures: list[str]) -> None:
    print("\n=== Workflows ===")
    old_wf, new_wf = old["workflows"], new["workflows"]
    print(f"  old: {old_wf}")
    print(f"  new: {new_wf}")
    if not old_wf and not new_wf:
        # Both agree there are no HTTP-annotated entry points in this repo --
        # that's agreement, not a divergence (e.g. a non-Spring-MVC repo).
        pass
    elif not old_wf or not new_wf:
        failures.append("one graph has Workflows and the other has none")
    else:
        matched = any(o["method"] == n["method"] and o["path"] == n["path"] for o in old_wf for n in new_wf)
        if not matched:
            failures.append("no matching Workflow (method+path) found between old and new graphs")

    print("\n=== Domains ===")
    old_dom, new_dom = old["domains"], new["domains"]
    print(f"  old: {len(old_dom)} multi-member domains")
    print(f"  new: {len(new_dom)} multi-member domains")
    if not old_dom and not new_dom:
        pass
    elif not old_dom or not new_dom:
        failures.append("one graph has multi-member Domains and the other has none")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture_path", nargs="?",
                     default=str(REPO_ROOT / "e2e-test" / "fixtures" / "mini-spring-app"))
    ap.add_argument("--old-bin", default=str(DEFAULT_OLD_BIN))
    ap.add_argument("--new-bin", default=str(DEFAULT_NEW_BIN))
    args = ap.parse_args()

    fixture_path = Path(args.fixture_path).resolve()
    old_bin = Path(args.old_bin)
    new_bin = Path(args.new_bin)

    with tempfile.TemporaryDirectory(prefix="compare-graphs-") as tmp:
        repo_name = fixture_path.name
        old_db_dir = Path(tmp) / "old"
        new_db_dir = Path(tmp) / "new"
        run_indexer(old_bin, fixture_path, old_db_dir, repo_name, ["--language", "jvm"])
        run_indexer(new_bin, fixture_path, new_db_dir, repo_name, ["--clear"])

        queries = build_queries()
        old = dump_graph("kuzu", old_db_dir / f"{repo_name}-db", queries)
        new = dump_graph("ladybug", new_db_dir / f"{repo_name}-db", queries)

        failures: list[str] = []
        check_counts(old, new, failures)
        # The identity/CALLS spot-checks below are hardcoded to
        # mini-spring-app's specific class names -- only meaningful when
        # that's the fixture being run. Against an arbitrary real repo they'd
        # just report noise, so skip them and rely on the counts/workflow/
        # domain checks (which are repo-agnostic) instead.
        if repo_name == "mini-spring-app":
            check_identity_spot_checks(old, new, failures)
            check_calls(old, new, failures)
        else:
            print(f"\n(skipping mini-spring-app-specific identity/CALLS spot-checks -- fixture is '{repo_name}')")
        check_workflows_and_domains(old, new, failures)

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
