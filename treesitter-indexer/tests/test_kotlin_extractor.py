"""Unit tests against small in-repo Kotlin snippets, mirroring
tests/test_php_extractor.py's and tests/test_js_extractor.py's style.
Every snippet uses normal multi-line formatting -- tree-sitter-kotlin can
spuriously error on compressed single-line declarations (see the plan's
Global Constraints)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kotlin.extractor import extract_repo  # noqa: E402


def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


class KotlinExtractorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _nodes_by_qn(self, graph_json):
        return {n["qualifiedName"]: n for n in graph_json["nodes"]}

    def _calls_by_ids(self, graph_json):
        return {(r["sourceId"], r["targetId"]): r["properties"] for r in graph_json["relationships"] if r["type"] == "CALLS"}

    def test_class_with_implicit_this_call(self):
        """THE Kotlin-specific CALLS case: a bare identifier call inside a
        method body implicitly means 'call this class's own method' -- no
        explicit `this.` required, unlike every other language here."""
        _write(self.repo, "app/service.kt", """package app

class Service {
    fun outer() {
        inner()
    }

    fun inner() {
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app.Service.outer"]["id"], by_qn["app.Service.inner"]["id"])]
        self.assertEqual(edge["reason"], "same-file")
        self.assertAlmostEqual(edge["confidence"], 0.95)

    def test_data_class_extraction(self):
        _write(self.repo, "app/order.kt", """package app

data class Order(val id: String, val status: String)
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["app.Order"]["type"], "DataClass")
        self.assertIn("app.Order.id", by_qn)
        self.assertEqual(by_qn["app.Order.id"]["type"], "Field")

    def test_sealed_class_with_subtypes(self):
        _write(self.repo, "app/result.kt", """package app

sealed class PaymentResult

class PaymentSuccess : PaymentResult()

data class PaymentFailure(val reason: String) : PaymentResult()
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["app.PaymentResult"]["type"], "SealedClass")
        self.assertEqual(by_qn["app.PaymentSuccess"]["type"], "Class")
        self.assertEqual(by_qn["app.PaymentFailure"]["type"], "DataClass")

        sealed_subtypes = [r for r in graph["relationships"] if r["type"] == "SEALED_SUBTYPE"]
        self.assertEqual(
            {r["targetId"] for r in sealed_subtypes},
            {by_qn["app.PaymentSuccess"]["id"], by_qn["app.PaymentFailure"]["id"]},
        )
        for r in sealed_subtypes:
            self.assertEqual(r["sourceId"], by_qn["app.PaymentResult"]["id"])

    def test_object_declaration_and_companion_object(self):
        _write(self.repo, "app/singleton.kt", """package app

object Singleton {
    fun doThing() {
    }
}

class WithCompanion {
    companion object {
        fun create(): WithCompanion {
            return WithCompanion()
        }
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["app.Singleton"]["type"], "ObjectDecl")
        companion_nodes = [n for n in graph["nodes"] if n["type"] == "CompanionObject"]
        self.assertEqual(len(companion_nodes), 1)
        companion_of = [r for r in graph["relationships"] if r["type"] == "COMPANION_OF"]
        self.assertEqual(len(companion_of), 1)
        self.assertEqual(companion_of[0]["sourceId"], companion_nodes[0]["id"])
        self.assertEqual(companion_of[0]["targetId"], by_qn["app.WithCompanion"]["id"])

    def test_extension_function(self):
        _write(self.repo, "app/ext.kt", """package app

fun String.shout(): String {
    return this.uppercase()
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["app.shout"]["type"], "ExtensionFunction")
        extension_of = [r for r in graph["relationships"] if r["type"] == "EXTENSION_OF"]
        self.assertEqual(len(extension_of), 1)
        self.assertEqual(extension_of[0]["sourceId"], by_qn["app.shout"]["id"])

    def test_suspend_function_calling_suspend_function(self):
        _write(self.repo, "app/async.kt", """package app

suspend fun fetchData(): String {
    return fetchMore()
}

suspend fun fetchMore(): String {
    return "x"
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(by_qn["app.fetchData"]["type"], "SuspendFunction")
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app.fetchData"]["id"], by_qn["app.fetchMore"]["id"])]
        self.assertEqual(edge["reason"], "same-file")
        suspends = [r for r in graph["relationships"] if r["type"] == "SUSPENDS"]
        self.assertEqual(
            {(r["sourceId"], r["targetId"]) for r in suspends},
            {(by_qn["app.fetchData"]["id"], by_qn["app.fetchMore"]["id"])},
        )

    def test_class_level_delegation(self):
        _write(self.repo, "app/delegate.kt", """package app

interface Helper {
    fun help(): String
}

class DelegatingClass(h: Helper) : Helper by h
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        delegates_to = [r for r in graph["relationships"] if r["type"] == "DELEGATES_TO"]
        self.assertEqual(len(delegates_to), 1)
        self.assertEqual(delegates_to[0]["sourceId"], by_qn["app.DelegatingClass"]["id"])
        self.assertEqual(delegates_to[0]["targetId"], by_qn["app.Helper"]["id"])

    def test_annotation_usage_on_class_and_method(self):
        """THE highest-value new piece: annotation-USAGE extraction (the old
        parser only ever detected annotation-declaration, never usage)."""
        _write(self.repo, "app/controller.kt", """package app

@RestController
class OrderController {
    @GetMapping("/orders/{id}")
    fun show(id: String): String {
        return "ok"
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        class_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                     and r["sourceId"] == by_qn["app.OrderController"]["id"]]
        self.assertEqual(len(class_ann), 1)
        method_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                      and r["sourceId"] == by_qn["app.OrderController.show"]["id"]]
        self.assertEqual(len(method_ann), 1)
        ann_node = next(n for n in graph["nodes"] if n["id"] == method_ann[0]["targetId"])
        self.assertEqual(ann_node["name"], "GetMapping")
        self.assertEqual(method_ann[0]["properties"]["value"], "/orders/{id}")

    def test_duck_typed_object_call_falls_through_to_global_tier(self):
        _write(self.repo, "app/channel.kt", """package app

class EmailChannel {
    fun notifyUser() {
    }
}
""")
        _write(self.repo, "app/dispatcher.kt", """package app

fun dispatchToChannel(channel: EmailChannel) {
    channel.notifyUser()
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app.dispatchToChannel"]["id"], by_qn["app.EmailChannel.notifyUser"]["id"])]
        self.assertEqual(edge["reason"], "global")
        self.assertAlmostEqual(edge["confidence"], 0.50)

    def test_new_class_resolves_to_constructor(self):
        _write(self.repo, "app/widget.kt", """package app

class Widget(val name: String)

fun make(name: String): Widget {
    return Widget(name)
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = self._calls_by_ids(graph)
        edge = calls[(by_qn["app.make"]["id"], by_qn["app.Widget.<init>"]["id"])]
        self.assertEqual(edge["reason"], "same-file")

    def test_http_dependency_detected_from_any_call_with_literal_url(self):
        _write(self.repo, "app/billing.kt", """package app

class BillingClient {
    fun charge(amount: Int): String {
        return fetchUrl("https://api.stripe.com/v1/charges")
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        props = [n for n in graph["nodes"] if n["type"] == "ConfigProperty"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["properties"]["code"], "https://api.stripe.com/v1/charges")

    def test_ktor_route_recognition(self):
        _write(self.repo, "app/routes.kt", """package app

fun showOrder(id: String): String {
    return "ok"
}

fun registerRoutes() {
    routing {
        get("/orders/{id}") {
            showOrder("1")
        }
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        has_ann = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"
                   and r["sourceId"] == by_qn["app.showOrder"]["id"]]
        self.assertEqual(len(has_ann), 1)
        ann_node = next(n for n in graph["nodes"] if n["id"] == has_ann[0]["targetId"])
        self.assertEqual(ann_node["name"], "Get")
        self.assertEqual(has_ann[0]["properties"]["value"], "/orders/{id}")


if __name__ == "__main__":
    unittest.main()
