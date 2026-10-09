"""Unit tests against small in-repo PHP snippets, mirroring
tests/test_python_extractor.py's style."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from php.extractor import extract_repo  # noqa: E402


def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


class PhpExtractorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _nodes_by_qn(self, graph_json):
        return {n["qualifiedName"]: n for n in graph_json["nodes"]}

    def test_namespaced_class_interface_and_property(self):
        _write(self.repo, "app/Order.php", """<?php
namespace App\\Models;

interface Priceable {
    public function price(): float;
}

class Order implements Priceable {
    public int $id;
    private string $status = "new";

    public function price(): float {
        return 1.0;
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)

        self.assertIn("App\\Models\\Priceable", by_qn)
        self.assertEqual(by_qn["App\\Models\\Priceable"]["type"], "Interface")
        self.assertIn("App\\Models\\Order", by_qn)
        self.assertEqual(by_qn["App\\Models\\Order"]["type"], "Class")
        self.assertIn("App\\Models\\Order.id", by_qn)
        self.assertIn("App\\Models\\Order.status", by_qn)
        self.assertIn("App\\Models\\Order.price", by_qn)
        self.assertEqual(by_qn["App\\Models\\Order.price"]["type"], "Method")

        implements = [r for r in graph["relationships"] if r["type"] == "IMPLEMENTS"]
        self.assertEqual(
            {(r["sourceId"], r["targetId"]) for r in implements},
            {(by_qn["App\\Models\\Order"]["id"], by_qn["App\\Models\\Priceable"]["id"])},
        )

    def test_trait_modeled_as_class_with_type_marker(self):
        _write(self.repo, "app/Loggable.php", """<?php
namespace App\\Concerns;

trait Loggable {
    protected $logger;
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        node = by_qn["App\\Concerns\\Loggable"]
        self.assertEqual(node["type"], "Class")
        self.assertEqual(node["properties"]["type"], "trait")

    def test_no_namespace_uses_root_package(self):
        _write(self.repo, "helpers.php", """<?php
class Helper {
    public function run() {}
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertIn("Helper", by_qn)
        contains = [r for r in graph["relationships"] if r["type"] == "CONTAINS"]
        pkg_nodes = [n for n in graph["nodes"] if n["type"] == "Package"]
        self.assertEqual(len(pkg_nodes), 1)
        self.assertEqual(pkg_nodes[0]["qualifiedName"], "")
        self.assertIn((pkg_nodes[0]["id"], by_qn["Helper"]["id"]), {(r["sourceId"], r["targetId"]) for r in contains})

    def test_typed_parameter_and_return_type_and_constructor(self):
        _write(self.repo, "app/Svc.php", """<?php
namespace App;

class Widget {}

class Svc {
    public function __construct(private Widget $widget) {
    }

    public function make(int $qty): Widget {
        return $this->widget;
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)

        self.assertIn("App\\Svc.__construct", by_qn)
        self.assertEqual(by_qn["App\\Svc.__construct"]["type"], "Constructor")

        # Property promotion creates BOTH a Parameter (HAS_PARAMETER from the
        # Constructor) and a Field (CONTAINS from the Class) -- real PHP
        # semantics: a promoted constructor param literally becomes a
        # property.
        self.assertIn("App\\Svc.widget", by_qn)
        self.assertEqual(by_qn["App\\Svc.widget"]["type"], "Field")

        has_param = [r for r in graph["relationships"] if r["type"] == "HAS_PARAMETER"
                     and r["sourceId"] == by_qn["App\\Svc.__construct"]["id"]]
        self.assertEqual(len(has_param), 1)

        returns = [r for r in graph["relationships"] if r["type"] == "RETURNS"
                   and r["sourceId"] == by_qn["App\\Svc.make"]["id"]]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0]["targetId"], by_qn["App\\Widget"]["id"])

    def test_class_constant_modeled_as_field_with_type_marker(self):
        _write(self.repo, "app/Order.php", """<?php
namespace App;

class Order {
    const STATUS_PENDING = "pending";
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        node = by_qn["App\\Order.STATUS_PENDING"]
        self.assertEqual(node["type"], "Field")
        self.assertEqual(node["properties"]["type"], "constant")

    def test_free_function_in_namespace(self):
        _write(self.repo, "app/helpers.php", """<?php
namespace App;

function format_price(float $amount): string {
    return "$" . $amount;
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertIn("App\\format_price", by_qn)
        self.assertEqual(by_qn["App\\format_price"]["type"], "Function")
        contains = [r for r in graph["relationships"] if r["type"] == "CONTAINS"]
        pkg = next(n for n in graph["nodes"] if n["type"] == "Package" and n["qualifiedName"] == "App")
        self.assertIn((pkg["id"], by_qn["App\\format_price"]["id"]), {(r["sourceId"], r["targetId"]) for r in contains})

    def _calls_by_ids(self, graph_json):
        return {(r["sourceId"], r["targetId"]): r["properties"] for r in graph_json["relationships"] if r["type"] == "CALLS"}

    def test_this_call_is_same_file(self):
        _write(self.repo, "svc.php", """<?php
class Service {
    public function outer() {
        return $this->inner();
    }
    public function inner() {
        return 1;
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["Service.outer"]["id"], by_qn["Service.inner"]["id"])]
        self.assertEqual(edge["reason"], "same-file")
        self.assertAlmostEqual(edge["confidence"], 0.95)

    def test_static_class_call_cross_file_is_import_resolved(self):
        _write(self.repo, "helper.php", """<?php
class Helper {
    public static function format($x) {
        return $x;
    }
}
""")
        _write(self.repo, "app.php", """<?php
class App {
    public function run($x) {
        return Helper::format($x);
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["App.run"]["id"], by_qn["Helper.format"]["id"])]
        self.assertEqual(edge["reason"], "import-resolved")
        self.assertAlmostEqual(edge["confidence"], 0.90)

    def test_free_function_call_same_file(self):
        _write(self.repo, "app.php", """<?php
function helper_fn($x) {
    return $x;
}
class App {
    public function run($x) {
        return helper_fn($x);
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["App.run"]["id"], by_qn["helper_fn"]["id"])]
        self.assertEqual(edge["reason"], "same-file")

    def test_duck_typed_object_call_falls_through_to_global_tier(self):
        """$service->process() where $service is a typed parameter, NOT
        $this -- out of scope for a real tier in this increment (see
        module docstring), so it must land on the 0.50 global-name
        fallback, by method name only, same as any ambiguous call."""
        _write(self.repo, "channel.php", """<?php
class EmailChannel {
    public function notifyUser() {
    }
}
""")
        _write(self.repo, "app.php", """<?php
function notifyAll($channel) {
    $channel->notifyUser();
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["notifyAll"]["id"], by_qn["EmailChannel.notifyUser"]["id"])]
        self.assertEqual(edge["reason"], "global")
        self.assertAlmostEqual(edge["confidence"], 0.50)

    def test_new_resolves_to_constructor(self):
        _write(self.repo, "app.php", """<?php
class Widget {
    public function __construct($name) {
    }
}
function make($name) {
    return new Widget($name);
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["make"]["id"], by_qn["Widget.__construct"]["id"])]
        self.assertEqual(edge["reason"], "same-file")

    def test_http_dependency_detected_from_any_call_with_literal_url(self):
        _write(self.repo, "svc.php", """<?php
class BillingClient {
    public function charge($amount) {
        return file_get_contents("https://api.stripe.com/v1/charges");
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        props = [n for n in graph["nodes"] if n["type"] == "ConfigProperty"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["properties"]["code"], "https://api.stripe.com/v1/charges")

    def test_plain_route_with_class_constant_handler_array(self):
        _write(self.repo, "routes.php", """<?php
class OrderController {
    public function show() {
    }
}
Route::get('/orders/{id}', [OrderController::class, 'show']);
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        has_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                   and r["sourceId"] == by_qn["OrderController.show"]["id"]]
        self.assertEqual(len(has_ann), 1)
        ann_node = next(n for n in graph["nodes"] if n["id"] == has_ann[0]["targetId"])
        self.assertEqual(ann_node["name"], "Get")
        self.assertEqual(has_ann[0]["properties"]["value"], "/orders/{id}")

    def test_route_with_string_handler_form(self):
        _write(self.repo, "routes.php", """<?php
class OrderController {
    public function store() {
    }
}
Route::post('/orders', 'OrderController@store');
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        has_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                   and r["sourceId"] == by_qn["OrderController.store"]["id"]]
        self.assertEqual(len(has_ann), 1)

    def test_middleware_group_tags_nested_routes(self):
        _write(self.repo, "routes.php", """<?php
class OrderController {
    public function store() {
    }
    public function index() {
    }
}
Route::middleware('auth')->group(function () {
    Route::post('/orders', [OrderController::class, 'store']);
    Route::get('/orders', [OrderController::class, 'index']);
});
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        for method_qn in ("OrderController.store", "OrderController.index"):
            has_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                       and r["sourceId"] == by_qn[method_qn]["id"]]
            names = {next(n for n in graph["nodes"] if n["id"] == r["targetId"])["name"] for r in has_ann}
            self.assertIn("Middleware", names)
            mw_edge = next(r for r in has_ann
                           if next(n for n in graph["nodes"] if n["id"] == r["targetId"])["name"] == "Middleware")
            self.assertEqual(mw_edge["properties"]["value"], "auth")


if __name__ == "__main__":
    unittest.main()
