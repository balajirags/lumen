"""
Extracts ConfigFile/ConfigProperty nodes from YAML, JSON, and .properties
files under a repo's resource root(s), plus REFERENCES_CONFIG edges linking
@Value("${...}")-annotated Java fields/parameters/methods (already extracted
by java/extractor.py into the same Graph) to the config keys they read.

Scope, deliberately: only files under src/main/resources (Spring's
convention) are scanned -- see repo_layout.detect_resources_root for why this
does NOT fall back to scanning the whole repo the way detect_source_root
does for Java. Values are flattened to dotted keys (spring.datasource.url)
regardless of source format, mirroring how Spring itself resolves
application.yml/.properties into one property namespace. Multi-document YAML
files (Spring's `---`-separated profile sections) are scoped per document
index so profile-specific overrides of the same key don't collide.
"""

from __future__ import annotations

import os
import re
import sys

from tree_sitter import Language, Node, Parser
import tree_sitter_yaml as tsyaml
import tree_sitter_json as tsjson

from graph_model import Graph
from repo_layout import detect_modules, detect_resources_root

YAML_LANGUAGE = Language(tsyaml.language())
JSON_LANGUAGE = Language(tsjson.language())

CONFIG_EXTENSIONS = (".yml", ".yaml", ".json", ".properties")

# Matches ${some.key} or ${some.key:defaultValue} -- Spring placeholder syntax.
_PLACEHOLDER_RE = re.compile(r"\$\{([^}:]+)(?::[^}]*)?\}")


def _text(src: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _strip_scalar_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _emit_config_file(graph: Graph, rel_path: str) -> str:
    file_id = f"configfile:{rel_path}"
    graph.add_node(file_id, "CONFIG_FILE", os.path.basename(rel_path), rel_path, path=rel_path, external=False)
    return file_id


def _emit_config_property(graph: Graph, file_id: str, rel_path: str, doc_suffix: str, dotted_key: str, value: str) -> None:
    if not dotted_key:
        return
    qualified_name = f"{rel_path}{doc_suffix}#{dotted_key}"
    prop_id = f"configprop:{qualified_name}"
    graph.add_node(prop_id, "CONFIG_PROPERTY", dotted_key, qualified_name,
                   code=value, path=rel_path, external=False)
    graph.add_rel(file_id, prop_id, "CONTAINS")


# --- YAML -------------------------------------------------------------------

def _walk_yaml_value(src: bytes, value_node: Node | None, prefix: str, emit_leaf) -> None:
    if value_node is None:
        emit_leaf(prefix, "")
        return

    node = value_node
    if node.type == "block_node":
        children = node.named_children
        node = children[0] if children else None
        if node is None:
            emit_leaf(prefix, "")
            return

    if node.type == "block_mapping":
        for pair in node.named_children:
            if pair.type != "block_mapping_pair":
                continue
            key_node = pair.child_by_field_name("key")
            val_node = pair.child_by_field_name("value")
            key_text = _strip_scalar_quotes(_text(src, key_node))
            new_prefix = f"{prefix}.{key_text}" if prefix else key_text
            _walk_yaml_value(src, val_node, new_prefix, emit_leaf)
    elif node.type == "block_sequence":
        items = []
        for item in node.named_children:
            if item.type == "block_sequence_item" and item.named_children:
                items.append(_strip_scalar_quotes(_text(src, item.named_children[0])))
        emit_leaf(prefix, ", ".join(items))
    else:
        # Leaf scalar (flow_node wrapping plain/quoted scalar). Inline flow
        # mappings/sequences ({a: 1}, [1, 2]) are treated as an opaque leaf
        # too -- an accepted simplification, these are rare in Spring config.
        emit_leaf(prefix, _strip_scalar_quotes(_text(src, node)))


def _extract_yaml_file(path: str, rel_path: str, graph: Graph, parser: Parser) -> None:
    with open(path, "rb") as fh:
        src = fh.read()
    tree = parser.parse(src)
    file_id = _emit_config_file(graph, rel_path)

    documents = [c for c in tree.root_node.named_children if c.type == "document"]
    multi_doc = len(documents) > 1
    for doc_index, doc in enumerate(documents):
        doc_suffix = f"[doc{doc_index}]" if multi_doc else ""
        content = next(iter(doc.named_children), None)
        if content is None:
            continue

        def emit_leaf(dotted_key: str, value: str, _suffix=doc_suffix) -> None:
            _emit_config_property(graph, file_id, rel_path, _suffix, dotted_key, value)

        _walk_yaml_value(src, content, "", emit_leaf)


# --- JSON --------------------------------------------------------------------

def _json_leaf_text(src: bytes, node: Node) -> str:
    if node.type == "string":
        content = next((c for c in node.named_children if c.type == "string_content"), None)
        return _text(src, content) if content is not None else ""
    return _text(src, node)


def _walk_json_value(src: bytes, node: Node, prefix: str, emit_leaf) -> None:
    if node.type == "object":
        for pair in node.named_children:
            if pair.type != "pair":
                continue
            key_node = pair.child_by_field_name("key")
            val_node = pair.child_by_field_name("value")
            key_text = _json_leaf_text(src, key_node) if key_node is not None else ""
            new_prefix = f"{prefix}.{key_text}" if prefix else key_text
            if val_node is not None:
                _walk_json_value(src, val_node, new_prefix, emit_leaf)
    elif node.type == "array":
        items = [_json_leaf_text(src, c) for c in node.named_children]
        emit_leaf(prefix, ", ".join(items))
    else:
        emit_leaf(prefix, _json_leaf_text(src, node))


def _extract_json_file(path: str, rel_path: str, graph: Graph, parser: Parser) -> None:
    with open(path, "rb") as fh:
        src = fh.read()
    tree = parser.parse(src)
    file_id = _emit_config_file(graph, rel_path)

    def emit_leaf(dotted_key: str, value: str) -> None:
        _emit_config_property(graph, file_id, rel_path, "", dotted_key, value)

    top = next(iter(tree.root_node.named_children), None)
    if top is not None:
        _walk_json_value(src, top, "", emit_leaf)


# --- .properties (plain key=value lines, no tree-sitter grammar needed) -----

def _extract_properties_file(path: str, rel_path: str, graph: Graph) -> None:
    file_id = _emit_config_file(graph, rel_path)
    # Line continuation (trailing backslash) and multi-line values are not
    # handled -- an accepted simplification for this first pass.
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            sep_idx = next((i for i, ch in enumerate(line) if ch in ("=", ":")), None)
            if sep_idx is None:
                continue
            key = line[:sep_idx].strip()
            value = line[sep_idx + 1:].strip()
            _emit_config_property(graph, file_id, rel_path, "", key, value)


# --- Driver -------------------------------------------------------------------

def extract_config(repo_path: str, graph: Graph) -> None:
    yaml_parser = Parser(YAML_LANGUAGE)
    json_parser = Parser(JSON_LANGUAGE)

    for module_root in detect_modules(repo_path):
        resources_root = detect_resources_root(module_root)
        if resources_root is None:
            continue
        for root, dirs, files in os.walk(resources_root):
            # "test"/"tests" belt-and-suspenders: detect_resources_root can
            # fall back to a bare src/ when a repo has no src/main at all, in
            # which case a sibling src/test/resources would otherwise still
            # get walked into.
            dirs[:] = [d for d in dirs if d not in (".git", "target", "build", "node_modules", ".venv", "test", "tests")]
            for fname in files:
                if not fname.endswith(CONFIG_EXTENSIONS):
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, repo_path)
                try:
                    if fname.endswith((".yml", ".yaml")):
                        _extract_yaml_file(full_path, rel_path, graph, yaml_parser)
                    elif fname.endswith(".json"):
                        _extract_json_file(full_path, rel_path, graph, json_parser)
                    elif fname.endswith(".properties"):
                        _extract_properties_file(full_path, rel_path, graph)
                except Exception as exc:
                    print(f"config extractor: failed to parse {rel_path}: {exc}", file=sys.stderr)

    link_config_usages(graph)


def link_config_usages(graph: Graph) -> None:
    """Links @Value("${...}")-annotated Field/Parameter/Method nodes (already
    in `graph` via HAS_ANNOTATION edges from java/extractor.py) to the
    ConfigProperty node(s) whose dotted key matches the placeholder."""
    key_to_prop_ids: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        if node["type"] == "CONFIG_PROPERTY":
            key_to_prop_ids.setdefault(node["name"], []).append(node["id"])
    if not key_to_prop_ids:
        return

    for rel in list(graph.relationships):
        if rel["type"] != "HAS_ANNOTATION":
            continue
        ann_node = graph.nodes.get(rel["targetId"])
        if ann_node is None or ann_node["name"] != "Value":
            continue
        match = _PLACEHOLDER_RE.search(rel["properties"].get("value") or "")
        if not match:
            continue
        key = match.group(1).strip()
        for prop_id in key_to_prop_ids.get(key, []):
            graph.add_rel(rel["sourceId"], prop_id, "REFERENCES_CONFIG")
