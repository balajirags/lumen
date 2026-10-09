"""Unit tests against small in-repo JS/TS snippets, mirroring
tests/test_php_extractor.py's style."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from js.extractor import extract_repo  # noqa: E402


def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


class JsExtractorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _nodes_by_qn(self, graph_json):
        return {n["qualifiedName"]: n for n in graph_json["nodes"]}

    def test_class_with_extends_and_methods(self):
        _write(self.repo, "app/widget.js", """
class Widget extends Base {
  constructor(name) {
    this.name = name;
  }
  render() {
    return this.name;
  }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)

        self.assertIn("app/widget.Widget", by_qn)
        self.assertEqual(by_qn["app/widget.Widget"]["type"], "Class")
        self.assertIn("app/widget.Widget.constructor", by_qn)
        self.assertEqual(by_qn["app/widget.Widget.constructor"]["type"], "Constructor")
        self.assertIn("app/widget.Widget.render", by_qn)
        self.assertEqual(by_qn["app/widget.Widget.render"]["type"], "Method")

        extends_rels = [r for r in graph["relationships"] if r["type"] == "EXTENDS"]
        self.assertEqual(len(extends_rels), 1)
        self.assertEqual(extends_rels[0]["sourceId"], by_qn["app/widget.Widget"]["id"])

    def test_top_level_function_and_arrow_function(self):
        _write(self.repo, "app/helpers.js", """
function helper(x) {
  return x;
}
const add = (a, b) => a + b;
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertIn("app/helpers.helper", by_qn)
        self.assertEqual(by_qn["app/helpers.helper"]["type"], "Function")
        self.assertIn("app/helpers.add", by_qn)
        self.assertEqual(by_qn["app/helpers.add"]["type"], "ArrowFunction")

        contains = [r for r in graph["relationships"] if r["type"] == "CONTAINS"]
        mod = next(n for n in graph["nodes"] if n["type"] == "Module" and n["qualifiedName"] == "app/helpers")
        self.assertIn((mod["id"], by_qn["app/helpers.helper"]["id"]), {(r["sourceId"], r["targetId"]) for r in contains})

    def test_export_default_class_still_extracted(self):
        _write(self.repo, "app/exported.js", """
export default class Exported {
  run() {}
}
export function named() {}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertIn("app/exported.Exported", by_qn)
        self.assertIn("app/exported.named", by_qn)

    def test_arrow_function_class_property_is_callable(self):
        """React class-component pattern: handleClick = () => {...} --
        modeled as a method-like callable so its body gets walked for
        CALLS, not just as an inert Field."""
        _write(self.repo, "app/clicker.js", """
class Clicker {
  handleClick = () => {
    this.doSomething();
  };
  doSomething() {}
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertIn("app/clicker.Clicker.handleClick", by_qn)
        self.assertEqual(by_qn["app/clicker.Clicker.handleClick"]["type"], "Method")

    def _calls_by_ids(self, graph_json):
        return {(r["sourceId"], r["targetId"]): r["properties"] for r in graph_json["relationships"] if r["type"] == "CALLS"}

    def test_this_method_call_is_same_file(self):
        """THE fix: the old engine built a wrong-shaped target id for
        this.method() and the edge was silently dropped."""
        _write(self.repo, "app/service.js", """
class Service {
  outer() {
    return this.inner();
  }
  inner() {
    return 1;
  }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app/service.Service.outer"]["id"], by_qn["app/service.Service.inner"]["id"])]
        self.assertEqual(edge["reason"], "same-file")
        self.assertAlmostEqual(edge["confidence"], 0.95)

    def test_relative_import_call_is_import_resolved(self):
        _write(self.repo, "app/helper.js", "export function format(x) { return x; }\n")
        _write(self.repo, "app/caller.js", """
import { format } from './helper';
function run(x) {
  return format(x);
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app/caller.run"]["id"], by_qn["app/helper.format"]["id"])]
        self.assertEqual(edge["reason"], "import-resolved")
        self.assertAlmostEqual(edge["confidence"], 0.90)

    def test_new_class_resolves_to_constructor(self):
        _write(self.repo, "app/widget.js", """
class Widget {
  constructor(name) {
    this.name = name;
  }
}
function make(name) {
  return new Widget(name);
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app/widget.make"]["id"], by_qn["app/widget.Widget.constructor"]["id"])]
        self.assertEqual(edge["reason"], "same-file")

    def test_duck_typed_object_call_falls_through_to_global_tier(self):
        _write(self.repo, "app/channel.js", """
class EmailChannel {
  notifyUser() {}
}
""")
        _write(self.repo, "app/dispatcher.js", """
function dispatchToChannel(channel) {
  channel.notifyUser();
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app/dispatcher.dispatchToChannel"]["id"], by_qn["app/channel.EmailChannel.notifyUser"]["id"])]
        self.assertEqual(edge["reason"], "global")
        self.assertAlmostEqual(edge["confidence"], 0.50)

    def test_arrow_function_calling_arrow_function_same_file(self):
        _write(self.repo, "app/math.js", """
const square = (x) => x * x;
const sumOfSquares = (a, b) => square(a) + square(b);
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app/math.sumOfSquares"]["id"], by_qn["app/math.square"]["id"])]
        self.assertEqual(edge["reason"], "same-file")

    def test_component_naming_heuristic(self):
        _write(self.repo, "app/widget.jsx", """
function Widget(props) {
  return <div>{props.label}</div>;
}
function useWidgetState() {
  return 1;
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["app/widget.Widget"]["type"], "Component")
        self.assertEqual(by_qn["app/widget.useWidgetState"]["type"], "Hook")

    def test_hook_call_emits_uses_hook_not_calls(self):
        _write(self.repo, "app/widget.jsx", """
function useCustomHook() {
  return 1;
}
function Widget(props) {
  const value = useCustomHook();
  return <div>{value}</div>;
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        uses_hook = [r for r in graph["relationships"] if r["type"] == "USES_HOOK"]
        self.assertEqual(
            {(r["sourceId"], r["targetId"]) for r in uses_hook},
            {(by_qn["app/widget.Widget"]["id"], by_qn["app/widget.useCustomHook"]["id"])},
        )
        calls = self._calls_by_ids(graph)
        self.assertNotIn((by_qn["app/widget.Widget"]["id"], by_qn["app/widget.useCustomHook"]["id"]), calls)

    def test_http_dependency_detected_from_any_call_with_literal_url(self):
        _write(self.repo, "app/billing.js", """
class BillingClient {
  charge(amount) {
    return fetch('https://api.stripe.com/v1/charges');
  }
}
""")
        graph = extract_repo(self.repo).to_json()
        props = [n for n in graph["nodes"] if n["type"] == "ConfigProperty"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["properties"]["code"], "https://api.stripe.com/v1/charges")

    def test_express_route_with_function_expression_handler(self):
        _write(self.repo, "app/routes.js", """
app.get('/orders/:id', function show(req, res) {
  res.send('ok');
});
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        has_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                   and r["sourceId"] == by_qn["app/routes.show"]["id"]]
        self.assertEqual(len(has_ann), 1)
        ann_node = next(n for n in graph["nodes"] if n["id"] == has_ann[0]["targetId"])
        self.assertEqual(ann_node["name"], "Get")
        self.assertEqual(has_ann[0]["properties"]["value"], "/orders/:id")

    def test_express_route_with_arrow_function_handler(self):
        _write(self.repo, "app/routes.js", """
const handleCreate = (req, res) => {
  res.send('created');
};
router.post('/orders', handleCreate);
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        has_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                   and r["sourceId"] == by_qn["app/routes.handleCreate"]["id"]]
        self.assertEqual(len(has_ann), 1)
        ann_node = next(n for n in graph["nodes"] if n["id"] == has_ann[0]["targetId"])
        self.assertEqual(ann_node["name"], "Post")

    def test_map_get_is_not_misdetected_as_a_route(self):
        """Regression guard for the false-positive risk the design
        explicitly flags: a plain `.get(key)` call with only one argument
        (no path-shaped first arg AND handler second arg) must not be
        treated as a route registration."""
        _write(self.repo, "app/cache.js", """
function read(key) {
  return cache.get(key);
}
""")
        graph = extract_repo(self.repo).to_json()
        has_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"]
        self.assertEqual(has_ann, [])

    def test_nestjs_decorator_route(self):
        _write(self.repo, "app/order.controller.ts", """
class OrderController {
  @Get('/orders/:id')
  show() {
    return 'ok';
  }
  @Post('/orders')
  create() {
    return 'created';
  }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        show_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                    and r["sourceId"] == by_qn["app/order.controller.OrderController.show"]["id"]]
        self.assertEqual(len(show_ann), 1)
        ann_node = next(n for n in graph["nodes"] if n["id"] == show_ann[0]["targetId"])
        self.assertEqual(ann_node["name"], "Get")
        self.assertEqual(show_ann[0]["properties"]["value"], "/orders/:id")

    def test_class_level_decorator_does_not_attach_to_first_method(self):
        """A class-level @Injectable() decorator is a child of
        class_declaration, not class_body -- must not be mistakenly paired
        with the first method inside the body."""
        _write(self.repo, "app/service.ts", """
@Injectable()
class OrderService {
  @Get('/x')
  show() {}
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        show_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                    and r["sourceId"] == by_qn["app/service.OrderService.show"]["id"]]
        self.assertEqual(len(show_ann), 1)
        ann_node = next(n for n in graph["nodes"] if n["id"] == show_ann[0]["targetId"])
        self.assertEqual(ann_node["name"], "Get")


if __name__ == "__main__":
    unittest.main()
