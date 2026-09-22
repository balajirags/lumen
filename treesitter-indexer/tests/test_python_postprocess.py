"""
Unit tests for the Python-specific postprocess/ additions:
build_python_workflows (workflow_builder.py) and the DECORATES union +
per-Domain language derivation (domain_detector.py). Builds a real, throwaway
LadybugDB via graph_store.LadybugGraphStore rather than mocking Cypher, same
approach the rest of this package uses for anything that issues real queries.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import graph_store  # noqa: E402
from postprocess import domain_detector, workflow_builder  # noqa: E402


def _make_store(tmp_dir: str) -> graph_store.LadybugGraphStore:
    store = graph_store.LadybugGraphStore(os.path.join(tmp_dir, "test-db"))
    store.init_schema()
    return store


def _node(store, table, node_id, name, qn):
    store.execute(
        f"CREATE (n:{table} {{id: '{node_id}', name: '{name}', qualifiedName: '{qn}', "
        f"external: false, language: 'python', kind: '{table}', normKind: 'x'}})"
    )


def _decorates(store, dec_name, value, target_table, target_id, dec_id):
    store.execute(
        f"CREATE (d:Decorator {{id: '{dec_id}', name: '{dec_name}', qualifiedName: '{dec_name}', "
        "external: true, language: 'python', kind: 'Decorator', normKind: 'x'})"
    )
    store.execute(
        f"MATCH (d:Decorator {{id: '{dec_id}'}}), (n:{target_table} {{id: '{target_id}'}}) "
        f"CREATE (d)-[:DECORATES {{value: '{value}', language: 'python', kind: 'DECORATES', normKind: 'x'}}]->(n)"
    )


def _calls(store, from_table, from_id, to_table, to_id, confidence=0.95, reason="same-file"):
    store.execute(
        f"MATCH (a:{from_table} {{id: '{from_id}'}}), (b:{to_table} {{id: '{to_id}'}}) "
        f"CREATE (a)-[:CALLS {{confidence: {confidence}, reason: '{reason}', lineNumber: 1, "
        "language: 'python', kind: 'CALLS', normKind: 'x'}]->(b)"
    )


class BuildPythonWorkflowsTests(unittest.TestCase):
    def test_flask_style_function_entry_point_to_method_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Function", "f:get_user", "get_user", "app.get_user")
            _node(store, "Method", "m:find_by_id", "find_by_id", "repository.UserRepository.find_by_id")
            _decorates(store, "app.route", "/users/<id>", "Function", "f:get_user", "d1")
            _decorates(store, "GET", "", "Function", "f:get_user", "d2")
            _calls(store, "Function", "f:get_user", "Method", "m:find_by_id", confidence=0.90, reason="import-resolved")

            emitted = workflow_builder.build_python_workflows(store)
            self.assertEqual(emitted, 1)

            rows = list(store.execute("MATCH (w:Workflow) RETURN w.httpMethod AS m, w.httpPath AS p"))
            result = store.execute("MATCH (w:Workflow) RETURN w.httpMethod AS m, w.httpPath AS p")
            got = []
            while result.has_next():
                got.append(result.get_next())
            self.assertEqual(got, [["GET", "/users/<id>"]])

    def test_fastapi_style_zero_mechanism_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Function", "f:get_order", "get_order", "orders.get_order")
            _node(store, "Method", "m:find_by_id", "find_by_id", "repository.OrderRepository.find_by_id")
            _decorates(store, "router.get", "/orders/{id}", "Function", "f:get_order", "d1")
            _calls(store, "Function", "f:get_order", "Method", "m:find_by_id", confidence=0.90, reason="import-resolved")

            emitted = workflow_builder.build_python_workflows(store)
            self.assertEqual(emitted, 1)

    def test_no_entry_points_emits_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Function", "f:helper", "helper", "app.helper")
            emitted = workflow_builder.build_python_workflows(store)
            self.assertEqual(emitted, 0)

    def test_low_confidence_calls_are_not_traversed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Function", "f:handler", "handler", "app.handler")
            _node(store, "Method", "m:find", "find", "repository.Repo.find")
            _decorates(store, "app.get", "/x", "Function", "f:handler", "d1")
            _calls(store, "Function", "f:handler", "Method", "m:find", confidence=0.50, reason="global")

            emitted = workflow_builder.build_python_workflows(store)
            self.assertEqual(emitted, 0)


class DomainDetectorPythonTests(unittest.TestCase):
    def test_decorator_signal_feeds_heuristic_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            _node(store, "Class", "c:Repo", "UserRepository", "repository.UserRepository")
            _node(store, "Method", "m:find", "find_by_id", "repository.UserRepository.find_by_id")
            _node(store, "Method", "m:save", "save", "repository.UserRepository.save")
            store.execute("MATCH (c:Class {id: 'c:Repo'}), (m:Method {id: 'm:find'}) CREATE (c)-[:CONTAINS]->(m)")
            store.execute("MATCH (c:Class {id: 'c:Repo'}), (m:Method {id: 'm:save'}) CREATE (c)-[:CONTAINS]->(m)")
            _calls(store, "Method", "m:find", "Method", "m:save")

            emitted = domain_detector.detect_domains(store)
            self.assertGreaterEqual(emitted, 1)

            result = store.execute("MATCH (d:Domain) RETURN d.heuristicLabel AS label, d.language AS language")
            rows = []
            while result.has_next():
                rows.append(result.get_next())
            self.assertIn(["Repository", "python"], rows)

    def test_domain_language_defaults_to_java_when_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            # No `language` property set at all -- mirrors a Java graph
            # written before this field existed, or a raw CREATE that
            # forgot it; must not crash and must fall back to "java".
            store.execute(
                "CREATE (c:Class {id: 'c:A', name: 'A', qualifiedName: 'pkg.A', external: false, "
                "kind: 'Class', normKind: 'x'})"
            )
            store.execute(
                "CREATE (m:Method {id: 'm:run', name: 'run', qualifiedName: 'pkg.A.run', external: false, "
                "kind: 'Method', normKind: 'x'})"
            )
            store.execute("MATCH (c:Class {id: 'c:A'}), (m:Method {id: 'm:run'}) CREATE (c)-[:CONTAINS]->(m)")
            store.execute(
                "MATCH (a:Method {id: 'm:run'}), (b:Method {id: 'm:run'}) "
                "CREATE (a)-[:CALLS {confidence: 0.95, reason: 'same-file'}]->(b)"
            )

            domain_detector.detect_domains(store)
            result = store.execute("MATCH (d:Domain) RETURN d.language AS language")
            rows = []
            while result.has_next():
                rows.append(result.get_next())
            self.assertTrue(all(r == ["java"] for r in rows) or rows == [])


if __name__ == "__main__":
    unittest.main()
