#!/usr/bin/env python3
"""
Runs a batch of named Cypher queries against one graph DB and prints the
results as JSON on stdout. Deliberately a standalone subprocess: kuzu's and
ladybug's native pybind11 extensions both register a "Database" type in the
same global pybind11 type registry, so importing both in one process raises
`ImportError: generic_type: type "Database" is already registered!` --
compare_graphs.py works around this by never importing both in the same
process, dispatching each engine's reads through a fresh subprocess instead.

Usage: echo '{"key": "MATCH (n) RETURN count(n) AS c"}' | \
       python3 _dump_graph.py --backend kuzu|ladybug --db-path PATH
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["kuzu", "ladybug"], required=True)
    ap.add_argument("--db-path", required=True)
    args = ap.parse_args()

    queries: dict[str, str] = json.load(sys.stdin)

    if args.backend == "kuzu":
        import kuzu
        db = kuzu.Database(args.db_path, read_only=True)
        conn = kuzu.Connection(db)
    else:
        import ladybug as lb
        db = lb.Database(args.db_path, read_only=True)
        conn = lb.Connection(db)

    results: dict[str, object] = {}
    for key, cypher in queries.items():
        try:
            result = conn.execute(cypher)
            cols = result.get_column_names()
            rows = []
            while result.has_next():
                rows.append(dict(zip(cols, result.get_next())))
            results[key] = rows
        except Exception as exc:
            results[key] = {"__error__": str(exc)}

    json.dump(results, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
