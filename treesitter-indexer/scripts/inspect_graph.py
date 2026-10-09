#!/usr/bin/env python3
"""
Human-readable summary of a LadybugDB graph written by cmg-java-ts --
lets you eyeball whether the extraction is producing useful signal without
going through lumen run or the pipeline's toolkit.py at all.

Usage: python3 inspect_graph.py <db-path>
       (db-path is the "<repo-name>-db" directory parse.py wrote to)
"""

from __future__ import annotations

import os
import sys

import ladybug as lb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from postprocess.http_routes import resolve_route  # noqa: E402


def q(conn, cypher: str) -> list[dict]:
    result = conn.execute(cypher)
    cols = result.get_column_names()
    out = []
    while result.has_next():
        out.append(dict(zip(cols, result.get_next())))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <db-path>", file=sys.stderr)
        return 1

    db_path = sys.argv[1]
    db = lb.Database(db_path, read_only=True)
    conn = lb.Connection(db)

    print(f"=== Graph summary: {db_path} ===\n")

    print("-- Node counts --")
    for table in ["Package", "Module", "Class", "Interface", "Enum", "Record", "Method",
                  "Constructor", "Function", "ArrowFunction", "Component", "Hook",
                  "AsyncFunction", "Generator", "Decorator",
                  "DataClass", "SealedClass", "SealedInterface", "ObjectDecl",
                  "CompanionObject", "ExtensionFunction", "SuspendFunction", "Property",
                  "Field", "Parameter", "AnnotationType", "Workflow", "Domain",
                  "ConfigFile", "ConfigProperty"]:
        try:
            c = q(conn, f"MATCH (n:{table}) RETURN count(n) AS c")[0]["c"]
            if c:
                print(f"  {table:15s} {c}")
        except Exception:
            pass

    print("\n-- Entry points (HTTP-annotated/decorated methods+functions, any framework) --")
    ann_by_method: dict[str, dict[str, str]] = {}
    for row in q(conn, "MATCH (m:Method)-[r:HAS_ANNOTATION]->(a:AnnotationType) "
                       "RETURN m.qualifiedName AS method, a.name AS annotation, r.value AS value"):
        ann_by_method.setdefault(row["method"], {})[row["annotation"]] = row["value"]
    # Function/ArrowFunction added for JS/TS -- Express/Fastify route
    # handlers are commonly a plain top-level Function or ArrowFunction,
    # not a class Method the way every prior annotation-based language's
    # routes have been (see js/calls.py's emit_calls_and_routes_in_file).
    # SuspendFunction added for Kotlin -- Spring/JAX-RS-annotated suspend
    # functions are a distinct table from Method (see kotlin/extractor.py).
    for table in ("Function", "ArrowFunction", "SuspendFunction"):
        for row in q(conn, f"MATCH (m:{table})-[r:HAS_ANNOTATION]->(a:AnnotationType) "
                           "RETURN m.qualifiedName AS method, a.name AS annotation, r.value AS value"):
            ann_by_method.setdefault(row["method"], {})[row["annotation"]] = row["value"]
    # Python has no HAS_ANNOTATION convention -- decorators land in
    # Decorator/DECORATES instead (see python/decorators.py).
    for row in q(conn, "MATCH (d:Decorator)-[r:DECORATES]->(n) "
                       "RETURN n.qualifiedName AS method, d.name AS annotation, r.value AS value"):
        ann_by_method.setdefault(row["method"], {})[row["annotation"]] = row["value"]
    for method, anns in sorted(ann_by_method.items()):
        route = resolve_route(anns)
        if route is not None:
            http_method, http_path = route
            print(f"  {http_method:8s} {http_path or '':20s} {method}")

    print("\n-- Workflows (entry point -> terminal traces) --")
    for row in q(conn, "MATCH (w:Workflow) RETURN w.name AS name, w.httpMethod AS method, "
                       "w.httpPath AS path, w.stepCount AS steps, w.type AS type"):
        print(f"  [{row['method']:6s} {row['path'] or '':20s}] {row['name']}  "
              f"({row['steps']} steps, {row['type']})")

    print("\n-- Domains (clustered by label propagation) --")
    for row in q(conn, "MATCH (d:Domain) RETURN d.name AS name, d.heuristicLabel AS label, "
                       "d.memberCount AS members, d.cohesion AS cohesion ORDER BY d.memberCount DESC"):
        print(f"  {row['name']:20s} label={row['label']:12s} members={row['members']:3d} "
              f"cohesion={row['cohesion']:.2f}")

    print("\n-- Sample CALLS edges (up to 20) --")
    for row in q(conn, "MATCH (a)-[c:CALLS]->(b) RETURN a.qualifiedName AS src, "
                       "b.qualifiedName AS tgt, c.confidence AS conf, c.reason AS reason LIMIT 20"):
        print(f"  {row['src']} -> {row['tgt']}  (conf={row['conf']}, {row['reason']})")

    print("\n-- Config properties (up to 30) --")
    for row in q(conn, "MATCH (f:ConfigFile)-[:CONTAINS]->(p:ConfigProperty) "
                       "RETURN f.path AS file, p.name AS key, p.code AS value LIMIT 30"):
        print(f"  [{row['file']}] {row['key']} = {row['value']}")

    print("\n-- Java code reading config (@Value) --")
    for row in q(conn, "MATCH (a)-[:REFERENCES_CONFIG]->(p:ConfigProperty) "
                       "RETURN a.qualifiedName AS src, p.name AS key, p.code AS value"):
        print(f"  {row['src']} reads {row['key']} = {row['value']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
