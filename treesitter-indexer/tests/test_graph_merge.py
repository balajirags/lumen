import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import Graph  # noqa: E402


def _graph(*nodes, rels=()):
    g = Graph()
    for node_id, node_type, name, props in nodes:
        g.add_node(node_id, node_type, name, name, **props)
    for src, tgt, rel_type in rels:
        g.add_rel(src, tgt, rel_type)
    return g


class GraphMergeTest(unittest.TestCase):
    def test_stamp_language_fills_only_missing(self):
        g = _graph(("class:a.A", "CLASS", "A", {}), ("class:a.B", "CLASS", "B", {"language": "kotlin"}),
                   rels=[("class:a.A", "class:a.B", "CALLS")])
        g.stamp_language("java")
        self.assertEqual(g.nodes["class:a.A"]["properties"]["language"], "java")
        self.assertEqual(g.nodes["class:a.B"]["properties"]["language"], "kotlin")
        self.assertEqual(g.relationships[0]["properties"]["language"], "java")

    def test_merge_combines_disjoint_graphs(self):
        a = _graph(("class:a.A", "CLASS", "A", {"language": "java"}))
        b = _graph(("function:b.f", "FUNCTION", "f", {"language": "python"}),
                   rels=[("function:b.f", "function:b.f", "CALLS")])
        self.assertEqual(a.merge(b), [])
        self.assertEqual(set(a.nodes), {"class:a.A", "function:b.f"})
        self.assertEqual(len(a.relationships), 1)

    def test_shared_placeholders_dedupe_silently(self):
        a = _graph(("class:external:Foo", "CLASS", "Foo", {"language": "java"}),
                   ("package:com.x", "PACKAGE", "x", {"language": "java"}))
        b = _graph(("class:external:Foo", "CLASS", "Foo", {"language": "python"}),
                   ("package:com.x", "PACKAGE", "x", {"language": "python"}))
        self.assertEqual(a.merge(b), [])
        self.assertEqual(a.nodes["class:external:Foo"]["properties"]["language"], "java")

    def test_real_id_collision_across_languages_is_reported_first_wins(self):
        a = _graph(("callable:app.run", "FUNCTION", "run", {"language": "kotlin"}))
        b = _graph(("callable:app.run", "FUNCTION", "run", {"language": "js"}))
        self.assertEqual(a.merge(b), ["callable:app.run"])
        self.assertEqual(a.nodes["callable:app.run"]["properties"]["language"], "kotlin")

    def test_same_language_duplicate_is_not_a_collision(self):
        a = _graph(("class:a.A", "CLASS", "A", {"language": "java"}))
        b = _graph(("class:a.A", "CLASS", "A", {"language": "java"}))
        self.assertEqual(a.merge(b), [])


if __name__ == "__main__":
    unittest.main()
