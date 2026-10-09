"""Unit tests for build_php_workflows -- mirrors
tests/test_python_postprocess.py's style exactly."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import graph_store  # noqa: E402
from postprocess import workflow_builder  # noqa: E402


def _make_store(tmp_dir: str) -> graph_store.LadybugGraphStore:
    store = graph_store.LadybugGraphStore(os.path.join(tmp_dir, "test-db"))
    store.init_schema()
    return store


def _esc(s: str) -> str:
    """Mirrors graph_store.py's own _escape -- PHP qualifiedNames contain
    literal backslashes (App\\Repositories\\OrderRepository), which must be
    escaped before interpolating into a Cypher string literal, same as a
    literal single-quote would need to be."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _node(store, table, node_id, name, qn, **extra):
    props = ", ".join(f"{k}: '{v}'" if isinstance(v, str) else f"{k}: {v}" for k, v in extra.items())
    extra_str = (", " + props) if props else ""
    store.execute(
        f"CREATE (n:{table} {{id: '{_esc(node_id)}', name: '{_esc(name)}', qualifiedName: '{_esc(qn)}', "
        f"external: false, language: 'php', kind: '{table}', normKind: 'x'{extra_str}}})"
    )


def _has_annotation(store, method_table, method_id, ann_name, value):
    ann_id = f"ann:{ann_name}"
    store.execute(
        f"MERGE (a:AnnotationType {{id: '{ann_id}'}}) "
        f"ON CREATE SET a.name = '{ann_name}', a.qualifiedName = '{ann_name}', a.external = true, "
        "a.language = 'php', a.kind = 'AnnotationType', a.normKind = 'x'"
    )
    store.execute(
        f"MATCH (m:{method_table} {{id: '{method_id}'}}), (a:AnnotationType {{id: '{ann_id}'}}) "
        f"CREATE (m)-[:HAS_ANNOTATION {{value: '{value}', language: 'php', kind: 'HAS_ANNOTATION', normKind: 'x'}}]->(a)"
    )


def _calls(store, from_table, from_id, to_table, to_id, confidence=0.95, reason="same-file"):
    store.execute(
        f"MATCH (a:{from_table} {{id: '{from_id}'}}), (b:{to_table} {{id: '{to_id}'}}) "
        f"CREATE (a)-[:CALLS {{confidence: {confidence}, reason: '{reason}', lineNumber: 1, "
        "language: 'php', kind: 'CALLS', normKind: 'x'}]->(b)"
    )


class BuildPhpWorkflowsTests(unittest.TestCase):
    def test_route_entry_point_to_repository_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Method", "m:show", "show", "OrderController.show")
            _node(store, "Method", "m:find", "find", "App\\Repositories\\OrderRepository.find")
            _has_annotation(store, "Method", "m:show", "Get", "/orders/{id}")
            _calls(store, "Method", "m:show", "Method", "m:find", confidence=0.90, reason="import-resolved")

            emitted = workflow_builder.build_php_workflows(store)
            self.assertEqual(emitted, 1)

            result = store.execute("MATCH (w:Workflow) RETURN w.httpMethod AS m, w.httpPath AS p")
            self.assertEqual(result.get_next(), ["GET", "/orders/{id}"])

    def test_middleware_annotation_does_not_prevent_route_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Method", "m:store", "store", "OrderController.store")
            _node(store, "Method", "m:save", "save", "App\\Repositories\\OrderRepository.save")
            _has_annotation(store, "Method", "m:store", "Post", "/orders")
            _has_annotation(store, "Method", "m:store", "Middleware", "auth")
            _calls(store, "Method", "m:store", "Method", "m:save", confidence=0.90, reason="import-resolved")

            emitted = workflow_builder.build_php_workflows(store)
            self.assertEqual(emitted, 1)

    def test_no_entry_points_emits_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Method", "m:helper", "helper", "Helper.helper")
            emitted = workflow_builder.build_php_workflows(store)
            self.assertEqual(emitted, 0)

    def test_low_confidence_calls_are_not_traversed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Method", "m:show", "show", "OrderController.show")
            _node(store, "Method", "m:find", "find", "App\\Repositories\\OrderRepository.find")
            _has_annotation(store, "Method", "m:show", "Get", "/orders/{id}")
            _calls(store, "Method", "m:show", "Method", "m:find", confidence=0.50, reason="global")

            emitted = workflow_builder.build_php_workflows(store)
            self.assertEqual(emitted, 0)

    def test_route_traverses_through_free_function_to_repository(self):
        """Exercises the PHP-specific Function-node BFS extension: a route
        calls a free helper function, which calls the repository -- this
        must still resolve to a Workflow, unlike build_jvm_workflows's
        Method-only BFS."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Method", "m:index", "index", "OrderController.index")
            _node(store, "Function", "f:format", "format_orders", "App\\format_orders")
            _node(store, "Method", "m:find", "findAll", "App\\Repositories\\OrderRepository.findAll")
            _has_annotation(store, "Method", "m:index", "Get", "/orders")
            _calls(store, "Method", "m:index", "Function", "f:format", confidence=0.95, reason="same-file")
            _calls(store, "Function", "f:format", "Method", "m:find", confidence=0.90, reason="import-resolved")

            emitted = workflow_builder.build_php_workflows(store)
            self.assertEqual(emitted, 1)


if __name__ == "__main__":
    unittest.main()
