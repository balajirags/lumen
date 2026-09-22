"""
Unit tests against small in-repo Python snippets (no real indexer/fixture
involvement -- see e2e-test/fixtures/mini-python-service/ + scripts/
compare_graphs_python.py for that end-to-end check), mirroring
tests/test_extractor.py's style.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from python.extractor import extract_repo  # noqa: E402


def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


class PythonExtractorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _nodes_by_qn(self, graph_json):
        return {n["qualifiedName"]: n for n in graph_json["nodes"]}

    def _calls_by_ids(self, graph_json):
        return {(r["sourceId"], r["targetId"]): r["properties"] for r in graph_json["relationships"] if r["type"] == "CALLS"}

    def test_class_method_field_extraction(self):
        _write(self.repo, "widget.py", """
class Widget:
    name: str
    id: int = 1

    def get_name(self):
        return self.name
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)

        self.assertIn("widget.Widget", by_qn)
        self.assertEqual(by_qn["widget.Widget"]["type"], "Class")
        self.assertIn("widget.Widget.name", by_qn)
        self.assertIn("widget.Widget.id", by_qn)
        self.assertIn("widget.Widget.get_name", by_qn)
        self.assertEqual(by_qn["widget.Widget.get_name"]["type"], "Method")

    def test_dataclass_fields_flow_through_the_same_path(self):
        """No dataclass-specific code needed -- @dataclass is just a generic
        decorator, and its fields are ordinary class-body AnnAssigns."""
        _write(self.repo, "models.py", """
@dataclass
class Item:
    name: str
    price: float
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertIn("models.Item.name", by_qn)
        self.assertIn("models.Item.price", by_qn)
        decorator_nodes = [n for n in graph["nodes"] if n["type"] == "Decorator" and n["name"] == "dataclass"]
        self.assertEqual(len(decorator_nodes), 1)

    def test_async_and_generator_functions_get_distinct_tables(self):
        _write(self.repo, "streams.py", """
async def fetch():
    pass

def gen():
    yield 1

def plain():
    pass
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["streams.fetch"]["type"], "AsyncFunction")
        self.assertEqual(by_qn["streams.gen"]["type"], "Generator")
        self.assertEqual(by_qn["streams.plain"]["type"], "Function")

    def test_nested_generator_does_not_misclassify_outer_function(self):
        _write(self.repo, "outer.py", """
def outer():
    def inner():
        yield 1
    return inner
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["outer.outer"]["type"], "Function")

    def test_typed_parameter_and_return_type_extraction(self):
        _write(self.repo, "orders.py", """
class Order:
    pass


def get_order(order_id: int) -> Order:
    return Order()
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        has_param = [r for r in graph["relationships"] if r["type"] == "HAS_PARAMETER"]
        self.assertEqual(len(has_param), 1)
        self.assertEqual(has_param[0]["sourceId"], by_qn["orders.get_order"]["id"])

        returns = [r for r in graph["relationships"] if r["type"] == "RETURNS"]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0]["sourceId"], by_qn["orders.get_order"]["id"])
        self.assertEqual(returns[0]["targetId"], by_qn["orders.Order"]["id"])

    def test_self_and_cls_are_not_emitted_as_parameters(self):
        _write(self.repo, "svc.py", """
class Service:
    def method(self, x):
        pass

    @classmethod
    def create(cls, x):
        pass
""")
        graph = extract_repo(self.repo).to_json()
        param_names = {n["name"] for n in graph["nodes"] if n["type"] == "Parameter"}
        self.assertNotIn("self", param_names)
        self.assertNotIn("cls", param_names)
        self.assertIn("x", param_names)

    def test_calls_confidence_self_call_is_same_file(self):
        _write(self.repo, "svc.py", """
class Service:
    def outer(self):
        return self.inner()

    def inner(self):
        return 1
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["svc.Service.outer"]["id"], by_qn["svc.Service.inner"]["id"])]
        self.assertEqual(edge["reason"], "same-file")
        self.assertAlmostEqual(edge["confidence"], 0.95)

    def test_calls_confidence_module_level_ctor_assignment_cross_file(self):
        """Closes the documented gap: `repo = UserRepository()` at module
        scope, then `repo.find_by_id(...)` read (without `global`) from
        inside a function below it -- must resolve at import-resolved tier,
        not fall through to the weaker global-name tier."""
        _write(self.repo, "repository.py", """
class UserRepository:
    def find_by_id(self, user_id):
        return {"id": user_id}
""")
        _write(self.repo, "app.py", """
from repository import UserRepository

repo = UserRepository()


def get_user(user_id):
    return repo.find_by_id(user_id)
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app.get_user"]["id"], by_qn["repository.UserRepository.find_by_id"]["id"])]
        self.assertEqual(edge["reason"], "import-resolved")
        self.assertAlmostEqual(edge["confidence"], 0.90)

    def test_calls_confidence_import_module_then_bare_call(self):
        """Closes the documented gap: `import x; x.foo()` for an internal
        `x` -- the old engine explicitly skips this case."""
        _write(self.repo, "notifications.py", "def send(x):\n    pass\n")
        _write(self.repo, "app.py", """
import notifications


def run(x):
    notifications.send(x)
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app.run"]["id"], by_qn["notifications.send"]["id"])]
        self.assertEqual(edge["reason"], "import-resolved")
        self.assertAlmostEqual(edge["confidence"], 0.90)

    def test_calls_confidence_duck_typed_call_uses_global_fallback(self):
        """Does not exist for Python today -- same-corpus name index: the
        called method's own NAME matches exactly one function/method
        anywhere in the corpus."""
        _write(self.repo, "channels.py", """
class EmailChannel:
    def notify_user(self):
        pass
""")
        _write(self.repo, "app.py", """
def notify_all(channel):
    channel.notify_user()
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app.notify_all"]["id"], by_qn["channels.EmailChannel.notify_user"]["id"])]
        self.assertEqual(edge["reason"], "global")
        self.assertAlmostEqual(edge["confidence"], 0.50)

    def test_calls_ambiguous_global_name_does_not_resolve(self):
        _write(self.repo, "a.py", "class A:\n    def run(self):\n        pass\n")
        _write(self.repo, "b.py", "class B:\n    def run(self):\n        pass\n")
        _write(self.repo, "app.py", "def call_it(x):\n    x.run()\n")
        graph = extract_repo(self.repo).to_json()
        calls = self._calls_by_ids(graph)
        self.assertEqual(len(calls), 0)

    def test_http_dependency_detected_from_any_call_with_literal_url(self):
        _write(self.repo, "svc.py", """
import requests


def charge(amount):
    return requests.get("https://api.stripe.com/v1/charges").json()
""")
        graph = extract_repo(self.repo).to_json()
        props = [n for n in graph["nodes"] if n["type"] == "ConfigProperty"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["properties"]["code"], "https://api.stripe.com/v1/charges")

    def test_fastapi_style_decorator_zero_extra_mechanism(self):
        """`router.get` tokenizes to a "get" verb the same way Micronaut's
        "Get" does -- no methods=[...]-style glue needed."""
        _write(self.repo, "orders.py", """
@router.get("/orders/{order_id}")
def get_order(order_id):
    pass
""")
        graph = extract_repo(self.repo).to_json()
        decorators = [(n["name"], next(r["properties"]["value"] for r in graph["relationships"]
                                       if r["type"] == "DECORATES" and r["sourceId"] == n["id"]))
                      for n in graph["nodes"] if n["type"] == "Decorator"]
        self.assertIn(("router.get", "/orders/{order_id}"), decorators)

    def test_flask_methods_kwarg_normalizes_to_a_verb_signal(self):
        _write(self.repo, "app.py", """
@app.route("/x", methods=["POST"])
def handler():
    pass
""")
        graph = extract_repo(self.repo).to_json()
        decorator_names = {n["name"] for n in graph["nodes"] if n["type"] == "Decorator"}
        self.assertIn("app.route", decorator_names)
        self.assertIn("POST", decorator_names)


if __name__ == "__main__":
    unittest.main()
