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

HTTP_ANNOTATIONS = {
    "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping",
}


def _rows(store, cypher: str) -> list[dict]:
    result = store.execute(cypher)
    cols = result.get_column_names()
    out = []
    while result.has_next():
        out.append(dict(zip(cols, result.get_next())))
    return out


def _resolve_http_method(anns: dict[str, str]) -> str:
    for ann, http in (("GetMapping", "GET"), ("PostMapping", "POST"), ("PutMapping", "PUT"),
                       ("DeleteMapping", "DELETE"), ("PatchMapping", "PATCH")):
        if ann in anns:
            return http
    return "HTTP"


def _resolve_http_path(anns: dict[str, str]) -> str | None:
    for ann in HTTP_ANNOTATIONS:
        val = anns.get(ann)
        if val:
            return val
    return None


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


def _bfs_and_emit(node_info: dict, calls_by_source: dict[str, list[str]], entry_id: str,
                   http_method: str, http_path: str | None, language: str,
                   emitted_workflow_ids: set[str], store) -> None:
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
            if _is_jvm_terminal(node_info[callee]):
                _emit_workflow(node_info, new_path, http_method, http_path, language, emitted_workflow_ids, store)
            else:
                queue.append(new_path)


def _emit_workflow(node_info: dict, path: list[str], http_method: str, http_path: str | None,
                    language: str, emitted_workflow_ids: set[str], store) -> None:
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
        # explicit. This increment's BFS only ever walks Method nodes.
        store.execute(
            f"MATCH (n:Method {{id: '{esc(node_id)}'}}), (w:Workflow {{id: '{esc(workflow_id)}'}}) "
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

    entry_points = [nid for nid, anns in ann_index.items() if any(a in HTTP_ANNOTATIONS for a in anns)]
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
    for entry_id in entry_points:
        anns = ann_index.get(entry_id, {})
        http_method = _resolve_http_method(anns)
        http_path = _resolve_http_path(anns)
        _bfs_and_emit(node_info, calls_by_source, entry_id, http_method, http_path, "java",
                      emitted_workflow_ids, store)

    print(f"WorkflowBuilder: emitted {len(emitted_workflow_ids)} workflows from "
          f"{len(entry_points)} entry points", file=sys.stderr)
    return len(emitted_workflow_ids)
