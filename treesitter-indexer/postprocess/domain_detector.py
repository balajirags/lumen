"""
Ported from indexer/app/.../DomainDetector.java (verified by direct read).
JVM strategy only -- PHP/React strategies are out of scope for this Java-first
increment. Reads/writes the persisted graph via Cypher, same rationale as
workflow_builder.py.

The Python increment (see the plan) extends this file's heuristic-label
lookup and its language tagging to be multi-language-aware, but deliberately
does NOT extend CLUSTERABLE_TABLES/_build_adjacency to cluster Python's
top-level Function nodes -- only Class/Method members (e.g. a Python
repository class) are clustered today. A Flask/FastAPI app that's mostly
free functions will therefore get sparser Domain coverage than a JVM app;
flagged as a known follow-up, not attempted in this increment.
"""

from __future__ import annotations

import sys

import graph_store
from postprocess.layer_classification import layer_from_annotation, layer_from_qualified_name

LABEL_PROP_ITERATIONS = 10

# Clusterable types for the JVM strategy -- deliberately excludes Constructor,
# Field, and Record (matches DomainDetector.java's isClusterableType switch,
# which only lists CLASS, INTERFACE, ENUM, METHOD for the JVM case). Also
# covers Python's Class/Method nodes for free (same table names), but not
# Python's Function table -- see module docstring.
CLUSTERABLE_TABLES = ["Class", "Interface", "Enum", "Method"]


def _rows(store, cypher: str) -> list[dict]:
    result = store.execute(cypher)
    cols = result.get_column_names()
    out = []
    while result.has_next():
        out.append(dict(zip(cols, result.get_next())))
    return out


def _esc(s) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'") if s is not None else ""


def _is_test_node(qn: str) -> bool:
    return (
        qn.endswith("Test") or qn.endswith("IT") or qn.endswith("Spec")
        or "Test." in qn or ".test." in qn
    )


def _get_production_nodes(store) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    for table in CLUSTERABLE_TABLES:
        rows = _rows(
            store,
            f"MATCH (n:{table}) WHERE n.external = false OR n.external IS NULL "
            f"RETURN n.id AS id, n.name AS name, n.qualifiedName AS qualifiedName, n.language AS language",
        )
        for row in rows:
            qn = row.get("qualifiedName") or ""
            if _is_test_node(qn):
                continue
            nodes[row["id"]] = {
                "name": row.get("name"), "qualifiedName": qn, "table": table,
                "language": row.get("language") or "java",
            }
    return nodes


def _build_adjacency(store, production_nodes: dict[str, dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {nid: set() for nid in production_nodes}

    call_rows = _rows(
        store,
        "MATCH (a:Method)-[c:CALLS]->(b:Method) WHERE c.confidence >= 0.9 "
        "RETURN a.id AS src, b.id AS tgt",
    )
    for row in call_rows:
        src, tgt = row["src"], row["tgt"]
        if src in adj and tgt in adj:
            adj[src].add(tgt)
            adj[tgt].add(src)

    for from_table in ("Class", "Interface", "Enum"):
        contains_rows = _rows(store, f"MATCH (a:{from_table})-[:CONTAINS]->(b:Method) RETURN a.id AS src, b.id AS tgt")
        for row in contains_rows:
            src, tgt = row["src"], row["tgt"]
            if src in adj and tgt in adj:
                adj[src].add(tgt)
                adj[tgt].add(src)

    return adj


def _dominant_neighbour_label(adj: dict[str, set[str]], labels: dict[str, str], node: str) -> str | None:
    neighbours = adj.get(node, set())
    if not neighbours:
        return None
    freq: dict[str, int] = {}
    for nb in neighbours:
        lbl = labels.get(nb)
        if lbl is not None:
            freq[lbl] = freq.get(lbl, 0) + 1
    if not freq:
        return None
    return max(freq.items(), key=lambda kv: kv[1])[0]


def _extract_segment(qn: str) -> str | None:
    if "." in qn:
        parts = qn.split(".")
        if len(parts) >= 2:
            return parts[-2]
    return None


def _derive_domain_name(production_nodes: dict[str, dict], members: list[str]) -> str:
    seg_freq: dict[str, int] = {}
    for mid in members:
        qn = production_nodes[mid]["qualifiedName"]
        seg = _extract_segment(qn)
        if seg:
            seg_freq[seg] = seg_freq.get(seg, 0) + 1
    if not seg_freq:
        return "Unknown"
    best = max(seg_freq.items(), key=lambda kv: kv[1])[0]
    return best[:1].upper() + best[1:] if best else "Unknown"


def _derive_domain_language(production_nodes: dict[str, dict], members: list[str]) -> str:
    """Was hardcoded to "java" before this graph could ever contain more than
    one language's nodes -- now derives it from the members' own `language`
    property (majority vote) so a Python-only Domain isn't mislabeled."""
    freq: dict[str, int] = {}
    for member_id in members:
        lang = production_nodes[member_id].get("language") or "java"
        freq[lang] = freq.get(lang, 0) + 1
    if not freq:
        return "java"
    return max(freq.items(), key=lambda kv: kv[1])[0]


def _derive_heuristic_label(store, production_nodes: dict[str, dict], members: list[str]) -> str:
    member_set = set(members)
    ann_freq: dict[str, int] = {}
    ann_rows = _rows(
        store,
        "MATCH (n:Method)-[r:HAS_ANNOTATION]->(a:AnnotationType) RETURN n.id AS nodeId, a.name AS annName",
    )
    class_ann_rows = _rows(
        store,
        "MATCH (n:Class)-[r:HAS_ANNOTATION]->(a:AnnotationType) RETURN n.id AS nodeId, a.name AS annName",
    )
    # Python has no HAS_ANNOTATION-based stereotype convention of its own --
    # decorators land in Decorator/DECORATES instead (see
    # python/decorators.py). Unioned in additively: layer_from_annotation
    # itself needs no changes, since a decorator name (e.g. "app.route",
    # "dataclass") tokenizes the same way an annotation name does.
    decorator_rows = _rows(
        store,
        "MATCH (d:Decorator)-[:DECORATES]->(n) RETURN n.id AS nodeId, d.name AS annName",
    )
    for row in ann_rows + class_ann_rows + decorator_rows:
        if row["nodeId"] not in member_set:
            continue
        layer = layer_from_annotation(row["annName"])
        if layer:
            ann_freq[layer] = ann_freq.get(layer, 0) + 1
    if ann_freq:
        return max(ann_freq.items(), key=lambda kv: kv[1])[0]

    # Fallback for codebases with no stereotype annotations at all (JAX-RS,
    # plain package-by-layer Java): the same role-word check applied to
    # package/qualifiedName instead of annotation names.
    pkg_freq: dict[str, int] = {}
    for m in members:
        layer = layer_from_qualified_name(production_nodes[m]["qualifiedName"])
        if layer:
            pkg_freq[layer] = pkg_freq.get(layer, 0) + 1
    if pkg_freq:
        return max(pkg_freq.items(), key=lambda kv: kv[1])[0]
    return "Mixed"


def _compute_cohesion(store, member_set: set[str]) -> float:
    call_rows = _rows(store, "MATCH (a:Method)-[c:CALLS]->(b:Method) RETURN a.id AS src, b.id AS tgt")
    internal = boundary = 0
    for row in call_rows:
        src_in = row["src"] in member_set
        tgt_in = row["tgt"] in member_set
        if src_in and tgt_in:
            internal += 1
        elif src_in or tgt_in:
            boundary += 1
    total = internal + boundary
    return 1.0 if total == 0 else internal / total


def detect_domains(store) -> int:
    """Returns the number of Domain nodes emitted."""
    production_nodes = _get_production_nodes(store)
    if not production_nodes:
        return 0

    adj = _build_adjacency(store, production_nodes)

    labels: dict[str, str] = {nid: nid for nid in production_nodes}
    for _ in range(LABEL_PROP_ITERATIONS):
        changed = False
        for node in production_nodes:
            dominant = _dominant_neighbour_label(adj, labels, node)
            if dominant is not None and dominant != labels[node]:
                labels[node] = dominant
                changed = True
        if not changed:
            break

    communities: dict[str, list[str]] = {}
    for node, label in labels.items():
        communities.setdefault(label, []).append(node)

    used_ids: set[str] = set()
    emitted = 0
    for members in communities.values():
        if len(members) < 2:
            continue

        domain_name = _derive_domain_name(production_nodes, members)
        heuristic_label = _derive_heuristic_label(store, production_nodes, members)
        cohesion = _compute_cohesion(store, set(members))
        language = _derive_domain_language(production_nodes, members)

        base_id = f"domain:{language}:{domain_name.lower().replace(' ', '-')}"
        domain_id = base_id
        suffix = 0
        while domain_id in used_ids:
            suffix += 1
            domain_id = f"{base_id}-{suffix}"
        used_ids.add(domain_id)

        store.execute(
            "CREATE (d:Domain {"
            f"id: '{_esc(domain_id)}', name: '{_esc(domain_name)}', qualifiedName: '{_esc(domain_name)}', "
            f"heuristicLabel: '{_esc(heuristic_label)}', cohesion: {cohesion}, memberCount: {len(members)}, "
            f"language: '{_esc(language)}', kind: 'Domain', normKind: '{graph_store.infer_norm_kind('DOMAIN')}'"
            "})"
        )
        for member_id in members:
            # IN_DOMAIN's FROM side spans several tables; an explicit label is
            # required for the same reason noted in workflow_builder.py.
            member_table = production_nodes[member_id]["table"]
            store.execute(
                f"MATCH (n:{member_table} {{id: '{_esc(member_id)}'}}), (d:Domain {{id: '{_esc(domain_id)}'}}) "
                f"CREATE (n)-[:IN_DOMAIN {{language: '{_esc(language)}', kind: 'IN_DOMAIN', "
                f"normKind: '{graph_store.infer_rel_norm_kind('IN_DOMAIN')}'}}]->(d)"
            )
        emitted += 1

    _update_workflow_types(store)
    print(f"DomainDetector: emitted {emitted} domains from {len(production_nodes)} production nodes",
          file=sys.stderr)
    return emitted


def _update_workflow_types(store) -> None:
    node_to_domain: dict[str, str] = {}
    for row in _rows(store, "MATCH (n)-[:IN_DOMAIN]->(d:Domain) RETURN n.id AS nodeId, d.id AS domainId"):
        node_to_domain[row["nodeId"]] = row["domainId"]

    workflow_domains: dict[str, set[str]] = {}
    for row in _rows(store, "MATCH (n)-[:WORKFLOW_STEP]->(w:Workflow) RETURN n.id AS nodeId, w.id AS workflowId"):
        domain = node_to_domain.get(row["nodeId"])
        if domain:
            workflow_domains.setdefault(row["workflowId"], set()).add(domain)

    for workflow_id, domains in workflow_domains.items():
        wf_type = "cross-domain" if len(domains) > 1 else "intra-domain"
        store.execute(f"MATCH (w:Workflow {{id: '{_esc(workflow_id)}'}}) SET w.type = '{_esc(wf_type)}'")
