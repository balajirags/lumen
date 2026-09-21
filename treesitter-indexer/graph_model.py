"""
Shared in-memory graph model, produced by both java/extractor.py and
config/extractor.py before being merged and written by graph_store.py.
Extracted out of java/extractor.py so config/extractor.py doesn't need to
import from the java package (and vice versa).
"""

from __future__ import annotations


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.relationships: list[dict] = []

    def add_node(self, node_id, node_type, name, qualified_name, **props):
        if node_id in self.nodes:
            return
        self.nodes[node_id] = {
            "id": node_id, "type": node_type, "name": name,
            "qualifiedName": qualified_name, "properties": props,
        }

    def has_node(self, node_id) -> bool:
        return node_id in self.nodes

    def add_rel(self, source_id, target_id, rel_type, **props):
        self.relationships.append({
            "sourceId": source_id, "targetId": target_id, "type": rel_type, "properties": props,
        })

    def to_json(self) -> dict:
        return {"nodes": list(self.nodes.values()), "relationships": self.relationships}
