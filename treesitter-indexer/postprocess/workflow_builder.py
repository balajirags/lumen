"""
Ported from indexer/app/.../WorkflowBuilder.java (verified by direct read).
JVM strategy only -- PHP/React strategies are out of scope for this Java-first
increment.

Deliberately reads/writes the PERSISTED graph via Cypher instead of an
in-memory CodeGraph object. This is a line-for-line-faithful port of the exact
same BFS logic, and it is also the fix for "only benefits cmg-java's own
in-memory graph": this runs against whatever is actually in the DB, not just
whatever one process happened to parse in memory.
"""

from __future__ import annotations

import sys
from collections import deque

import graph_store
from postprocess.http_routes import resolve_route


def _rows(store, cypher: str) -> list[dict]:
    result = store.execute(cypher)
    cols = result.get_column_names()
    out = []
    while result.has_next():
        out.append(dict(zip(cols, result.get_next())))
    return out


def _is_jvm_terminal(info: dict) -> bool:
    qn = info.get("qualifiedName") or ""
    name = (info.get("name") or "")
    name_lower = name.lower()
    return (
        ".repository." in qn
        or "producer" in name_lower
        or "kafkatemplate" in name_lower
        or "KafkaProducer" in qn
        or "EventPublisher" in qn
    )


def _is_python_terminal(info: dict) -> bool:
    """Python lacks Java's package-per-layer convention (`.repository.`), so
    this matches the qualifiedName/name substrings directly instead --
    mirrors the fixture's `repository.UserRepository`/`repository.py` shape."""
    qn = (info.get("qualifiedName") or "").lower()
    name = (info.get("name") or "").lower()
    return "repository" in qn or "dao" in qn or "producer" in name or "publisher" in name


def _bfs_and_emit(node_info: dict, calls_by_source: dict[str, list[str]], entry_id: str,
                   http_method: str, http_path: str | None, language: str,
                   emitted_workflow_ids: set[str], store,
                   is_terminal=_is_jvm_terminal, node_table: dict[str, str] | None = None) -> None:
    queue: deque[list[str]] = deque()
    queue.append([entry_id])
    visited: set[str] = set()

    while queue:
        path = queue.popleft()
        current = path[-1]
        if current in visited:
            continue
        visited.add(current)

        for callee in calls_by_source.get(current, []):
            if callee in visited or callee not in node_info:
                continue
            new_path = path + [callee]
            if is_terminal(node_info[callee]):
                _emit_workflow(node_info, new_path, http_method, http_path, language, emitted_workflow_ids, store,
                                node_table=node_table)
            else:
                queue.append(new_path)


def _emit_workflow(node_info: dict, path: list[str], http_method: str, http_path: str | None,
                    language: str, emitted_workflow_ids: set[str], store,
                    node_table: dict[str, str] | None = None) -> None:
    if len(path) < 2:
        return
    entry = node_info[path[0]]
    terminal = node_info[path[-1]]
    entry_qn = entry.get("qualifiedName") or path[0]
    terminal_qn = terminal.get("qualifiedName") or path[-1]
    workflow_id = f"workflow:{entry_qn}→{terminal_qn}:{hash(tuple(path))}"
    if workflow_id in emitted_workflow_ids:
        return
    emitted_workflow_ids.add(workflow_id)

    def cap(name: str) -> str:
        return name[:1].upper() + name[1:] if name else name

    display_name = f"{cap(entry.get('name') or '')} → {cap(terminal.get('name') or '')}"

    def esc(s) -> str:
        return str(s).replace("\\", "\\\\").replace("'", "\\'") if s is not None else ""

    store.execute(
        "CREATE (w:Workflow {"
        f"id: '{esc(workflow_id)}', name: '{esc(display_name)}', qualifiedName: '{esc(display_name)}', "
        f"entryPointId: '{esc(path[0])}', terminalId: '{esc(path[-1])}', stepCount: {len(path)}, "
        f"httpMethod: '{esc(http_method)}', httpPath: '{esc(http_path or '')}', "
        f"language: '{esc(language)}', kind: 'Workflow', normKind: '{graph_store.infer_norm_kind('WORKFLOW')}'"
        "})"
    )

    emitted_steps: set[str] = set()
    for i, node_id in enumerate(path, start=1):
        step_key = f"{node_id}:{i}"
        if step_key in emitted_steps:
            continue
        emitted_steps.add(step_key)
        # WORKFLOW_STEP's FROM side spans several tables (Method/Function/
        # AsyncFunction/ArrowFunction/Component); Kuzu/LadybugDB can't CREATE a
        # rel off an untyped multi-table match, so the source label must be
        # explicit. The JVM BFS only ever walks Method nodes (node_table is
        # None, defaulting below); Python's BFS mixes Function and Method
        # nodes in the same path, so it passes an explicit id->table map.
        table = (node_table or {}).get(node_id, "Method")
        store.execute(
            f"MATCH (n:{table} {{id: '{esc(node_id)}'}}), (w:Workflow {{id: '{esc(workflow_id)}'}}) "
            f"CREATE (n)-[:WORKFLOW_STEP {{step: {i}, language: '{esc(language)}', "
            f"kind: 'WORKFLOW_STEP', normKind: '{graph_store.infer_rel_norm_kind('WORKFLOW_STEP')}'}}]->(w)"
        )


def build_jvm_workflows(store) -> int:
    """Returns the number of Workflow nodes emitted."""
    ann_rows = _rows(
        store,
        "MATCH (n:Method)-[r:HAS_ANNOTATION]->(a:AnnotationType) "
        "RETURN n.id AS nodeId, a.name AS annName, r.value AS value",
    )
    ann_index: dict[str, dict[str, str]] = {}
    for row in ann_rows:
        ann_index.setdefault(row["nodeId"], {})[row["annName"]] = row["value"]

    # resolve_route() is framework-agnostic (see http_routes.py): it doesn't
    # know "Spring" or "JAX-RS", only that an annotation name looks verb-like
    # and/or some annotation's value looks like a path. This is what lets
    # Micronaut's @Get, JAX-RS's @GET + @Path, and Spring's @GetMapping all
    # resolve as entry points without any of those names being hardcoded.
    routes = {nid: resolve_route(anns) for nid, anns in ann_index.items()}
    entry_points = {nid: route for nid, route in routes.items() if route is not None}
    if not entry_points:
        return 0

    node_rows = _rows(store, "MATCH (n:Method) RETURN n.id AS id, n.name AS name, n.qualifiedName AS qualifiedName")
    node_info = {row["id"]: row for row in node_rows}

    call_rows = _rows(
        store,
        "MATCH (a:Method)-[c:CALLS]->(b:Method) WHERE c.confidence >= 0.9 "
        "RETURN a.id AS src, b.id AS tgt",
    )
    calls_by_source: dict[str, list[str]] = {}
    for row in call_rows:
        calls_by_source.setdefault(row["src"], []).append(row["tgt"])

    emitted_workflow_ids: set[str] = set()
    for entry_id, (http_method, http_path) in entry_points.items():
        _bfs_and_emit(node_info, calls_by_source, entry_id, http_method, http_path, "java",
                      emitted_workflow_ids, store)

    print(f"WorkflowBuilder: emitted {len(emitted_workflow_ids)} workflows from "
          f"{len(entry_points)} entry points", file=sys.stderr)
    return len(emitted_workflow_ids)


_PYTHON_CALLABLE_TABLES = ["Function", "Method", "AsyncFunction"]


def build_python_workflows(store) -> int:
    """Returns the number of Workflow nodes emitted. Parallel to (not a
    modification of) build_jvm_workflows -- see the plan's cross-cutting
    postprocess/ section. Python has no HAS_ANNOTATION-based routing
    convention of its own (decorators are captured via Decorator/DECORATES
    instead -- see python/decorators.py), so the annotation-gathering query
    below unions both shapes into the exact same {nodeId: {name: value}}
    dict resolve_route() already consumes unchanged: the HAS_ANNOTATION half
    is a harmless no-op today (Python never populates it) and future-proofs
    against a later annotation-shaped mechanism.
    """
    ann_rows = _rows(
        store,
        "MATCH (n:Function)-[r:HAS_ANNOTATION]->(a:AnnotationType) "
        "RETURN n.id AS nodeId, a.name AS annName, r.value AS value "
        "UNION "
        "MATCH (n:Method)-[r:HAS_ANNOTATION]->(a:AnnotationType) "
        "RETURN n.id AS nodeId, a.name AS annName, r.value AS value "
        "UNION "
        "MATCH (d:Decorator)-[r:DECORATES]->(n) "
        "RETURN n.id AS nodeId, d.name AS annName, r.value AS value",
    )
    ann_index: dict[str, dict[str, str]] = {}
    for row in ann_rows:
        ann_index.setdefault(row["nodeId"], {})[row["annName"]] = row["value"]

    routes = {nid: resolve_route(anns) for nid, anns in ann_index.items()}
    entry_points = {nid: route for nid, route in routes.items() if route is not None}
    if not entry_points:
        return 0

    node_info: dict[str, dict] = {}
    node_table: dict[str, str] = {}
    for table in _PYTHON_CALLABLE_TABLES:
        for row in _rows(store, f"MATCH (n:{table}) RETURN n.id AS id, n.name AS name, n.qualifiedName AS qualifiedName"):
            node_info[row["id"]] = row
            node_table[row["id"]] = table

    calls_by_source: dict[str, list[str]] = {}
    for from_table in _PYTHON_CALLABLE_TABLES:
        for to_table in _PYTHON_CALLABLE_TABLES:
            rows = _rows(
                store,
                f"MATCH (a:{from_table})-[c:CALLS]->(b:{to_table}) WHERE c.confidence >= 0.9 "
                "RETURN a.id AS src, b.id AS tgt",
            )
            for row in rows:
                calls_by_source.setdefault(row["src"], []).append(row["tgt"])

    emitted_workflow_ids: set[str] = set()
    for entry_id, (http_method, http_path) in entry_points.items():
        _bfs_and_emit(node_info, calls_by_source, entry_id, http_method, http_path, "python",
                      emitted_workflow_ids, store, is_terminal=_is_python_terminal, node_table=node_table)

    print(f"WorkflowBuilder (Python): emitted {len(emitted_workflow_ids)} workflows from "
          f"{len(entry_points)} entry points", file=sys.stderr)
    return len(emitted_workflow_ids)


def _is_php_terminal(info: dict) -> bool:
    """"reposit" (not "repository") deliberately catches both "Repository"
    and the equally-common plural "Repositories" PHP/Laravel convention
    uses -- "Repositories".lower() does NOT contain the exact substring
    "repository" (differs at one character), so a straight copy of
    _is_python_terminal's check would silently miss every plural-named
    repository class, which is the MORE common Laravel naming convention."""
    qn = (info.get("qualifiedName") or "").lower()
    name = (info.get("name") or "").lower()
    return "reposit" in qn or "dao" in qn or "producer" in name or "publisher" in name


# Function is included alongside Method (unlike build_jvm_workflows, which is
# Method-only) because PHP methods commonly call free/global helper
# functions directly (Laravel's redirect()/response()/view(), or any other
# free function) -- exactly the case Task 2's Method->Function/
# Constructor->Function schema additions exist for. Without this, those
# edges would be schema-supported but never actually traversed by the BFS,
# silently missing real workflow steps that route through a helper function.
_PHP_CALLABLE_TABLES = ["Method", "Function"]


def build_php_workflows(store) -> int:
    """Returns the number of Workflow nodes emitted. Parallel to (not a
    modification of) build_jvm_workflows/build_python_workflows. PHP's
    routing signal was already converted to ordinary HAS_ANNOTATION edges
    at extraction time (see php/calls.py's emit_calls_and_routes_in_file,
    via _emit_route_annotation) -- so the entry-point-gathering half of this
    function needs no query union at all, it's a direct copy of
    build_jvm_workflows's. The BFS half additionally walks Function nodes
    (see _PHP_CALLABLE_TABLES), mirroring build_python_workflows's
    multi-table approach rather than build_jvm_workflows's Method-only one.
    """
    ann_rows = _rows(
        store,
        "MATCH (n:Method)-[r:HAS_ANNOTATION]->(a:AnnotationType) "
        "RETURN n.id AS nodeId, a.name AS annName, r.value AS value",
    )
    ann_index: dict[str, dict[str, str]] = {}
    for row in ann_rows:
        ann_index.setdefault(row["nodeId"], {})[row["annName"]] = row["value"]

    routes = {nid: resolve_route(anns) for nid, anns in ann_index.items()}
    entry_points = {nid: route for nid, route in routes.items() if route is not None}
    if not entry_points:
        return 0

    node_info: dict[str, dict] = {}
    node_table: dict[str, str] = {}
    for table in _PHP_CALLABLE_TABLES:
        for row in _rows(store, f"MATCH (n:{table}) RETURN n.id AS id, n.name AS name, n.qualifiedName AS qualifiedName"):
            node_info[row["id"]] = row
            node_table[row["id"]] = table

    calls_by_source: dict[str, list[str]] = {}
    for from_table in _PHP_CALLABLE_TABLES:
        for to_table in _PHP_CALLABLE_TABLES:
            rows = _rows(
                store,
                f"MATCH (a:{from_table})-[c:CALLS]->(b:{to_table}) WHERE c.confidence >= 0.9 "
                "RETURN a.id AS src, b.id AS tgt",
            )
            for row in rows:
                calls_by_source.setdefault(row["src"], []).append(row["tgt"])

    emitted_workflow_ids: set[str] = set()
    for entry_id, (http_method, http_path) in entry_points.items():
        _bfs_and_emit(node_info, calls_by_source, entry_id, http_method, http_path, "php",
                      emitted_workflow_ids, store, is_terminal=_is_php_terminal, node_table=node_table)

    print(f"WorkflowBuilder (PHP): emitted {len(emitted_workflow_ids)} workflows from "
          f"{len(entry_points)} entry points", file=sys.stderr)
    return len(emitted_workflow_ids)
