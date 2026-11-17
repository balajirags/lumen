"""
Shared in-memory graph model, produced by both java/extractor.py and
config/extractor.py before being merged and written by graph_store.py.
Extracted out of java/extractor.py so config/extractor.py doesn't need to
import from the java package (and vice versa).
"""

from __future__ import annotations


# Placeholder ids every language may synthesize for the same real-world entity;
# merging these across languages is intentional dedup, not a collision.
SHARED_ID_PREFIXES = ("class:external:", "package:", "annotationtype:")


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

    def stamp_language(self, language: str) -> None:
        """Tag every node/rel that doesn't already carry a language. Needed
        before merge(): graph_store.save() only takes ONE default language
        per call, so a merged save would otherwise mis-tag (e.g.) Java nodes,
        which rely on that default instead of stamping themselves."""
        for node in self.nodes.values():
            node["properties"].setdefault("language", language)
        for rel in self.relationships:
            rel["properties"].setdefault("language", language)

    def merge(self, other: "Graph") -> list[str]:
        """Fold `other` into this graph. Returns the ids that collided on
        something that is NOT a deliberately shared placeholder.

        Ids carry no language prefix, so two languages can emit the same id.
        Placeholders every language synthesizes for the same real-world thing
        (external classes, packages, annotation types) dedupe first-wins on
        purpose. Any other collision is kept first-wins too, but reported so
        the caller can surface it instead of silently attaching one
        language's edges to another language's node."""
        collisions: list[str] = []
        for node_id, node in other.nodes.items():
            existing = self.nodes.get(node_id)
            if existing is None:
                self.nodes[node_id] = node
            elif (not node_id.startswith(SHARED_ID_PREFIXES)
                  and existing["properties"].get("language") != node["properties"].get("language")):
                collisions.append(node_id)
        self.relationships.extend(other.relationships)
        return collisions

    def to_json(self) -> dict:
        return {"nodes": list(self.nodes.values()), "relationships": self.relationships}
