"""
Unit tests against small in-repo Java snippets (no real indexer/fixture
involvement -- see scripts/compare_graphs.py for that end-to-end check).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from java.extractor import extract_repo  # noqa: E402


def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _nodes_by_qn(self, graph_json):
        return {n["qualifiedName"]: n for n in graph_json["nodes"]}

    def test_class_method_field_extraction(self):
        _write(self.repo, "src/com/example/Widget.java", """
package com.example;

@Entity
public class Widget {
    @Id
    private Long id;
    private String name;

    public String getName() {
        return name;
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)

        self.assertIn("com.example.Widget", by_qn)
        self.assertEqual(by_qn["com.example.Widget"]["type"], "CLASS")
        self.assertIn("com.example.Widget.id", by_qn)
        self.assertIn("com.example.Widget.name", by_qn)
        self.assertIn("com.example.Widget.getName", by_qn)

        ann_edges = [r for r in graph["relationships"] if r["type"] == "HAS_ANNOTATION"]
        annotated_sources = {r["sourceId"] for r in ann_edges}
        widget_id = by_qn["com.example.Widget"]["id"]
        id_field_id = by_qn["com.example.Widget.id"]["id"]
        self.assertIn(widget_id, annotated_sources)
        self.assertIn(id_field_id, annotated_sources)

    def test_implements_and_overrides(self):
        _write(self.repo, "src/com/example/Greeter.java", """
package com.example;
public interface Greeter {
    String greet(String name);
}
""")
        _write(self.repo, "src/com/example/EnglishGreeter.java", """
package com.example;
public class EnglishGreeter implements Greeter {
    @Override
    public String greet(String name) {
        return "Hello " + name;
    }
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)

        implements_edges = [r for r in graph["relationships"] if r["type"] == "IMPLEMENTS"]
        self.assertEqual(len(implements_edges), 1)
        self.assertEqual(implements_edges[0]["sourceId"], by_qn["com.example.EnglishGreeter"]["id"])
        self.assertEqual(implements_edges[0]["targetId"], by_qn["com.example.Greeter"]["id"])

        overrides_edges = [r for r in graph["relationships"] if r["type"] == "OVERRIDES"]
        self.assertEqual(len(overrides_edges), 1)
        self.assertEqual(overrides_edges[0]["sourceId"], by_qn["com.example.EnglishGreeter.greet"]["id"])
        self.assertEqual(overrides_edges[0]["targetId"], by_qn["com.example.Greeter.greet"]["id"])

    def test_calls_confidence_tiers(self):
        _write(self.repo, "src/com/example/a/Caller.java", """
package com.example.a;
import com.example.b.Callee;
public class Caller {
    private final Callee callee;
    public Caller(Callee callee) { this.callee = callee; }
    public void run() {
        callee.doWork();
        helper();
    }
    private void helper() {}
}
""")
        _write(self.repo, "src/com/example/b/Callee.java", """
package com.example.b;
public class Callee {
    public void doWork() {}
}
""")
        graph = extract_repo(self.repo).to_json()
        by_qn = self._nodes_by_qn(graph)
        calls = {
            (r["sourceId"], r["targetId"]): r["properties"]
            for r in graph["relationships"] if r["type"] == "CALLS"
        }

        cross_pkg = calls[(by_qn["com.example.a.Caller.run"]["id"], by_qn["com.example.b.Callee.doWork"]["id"])]
        self.assertEqual(cross_pkg["reason"], "import-resolved")
        self.assertAlmostEqual(cross_pkg["confidence"], 0.90)

        same_type = calls[(by_qn["com.example.a.Caller.run"]["id"], by_qn["com.example.a.Caller.helper"]["id"])]
        self.assertEqual(same_type["reason"], "same-file")
        self.assertAlmostEqual(same_type["confidence"], 0.95)

    def test_http_dependency_detected_from_any_method_call_with_literal_url(self):
        """No method-name allowlist: an arbitrary custom method with a literal
        URL argument is captured the same way RestTemplate/WebClient would be."""
        _write(self.repo, "src/com/example/PaymentService.java", """
package com.example;
public class PaymentService {
    public String charge(String amount) {
        return myCustomHttpHelper("https://api.stripe.com/v1/charges", amount);
    }
    private String myCustomHttpHelper(String url, String body) { return null; }
}
""")
        graph = extract_repo(self.repo).to_json()
        props = [n for n in graph["nodes"] if n["type"] == "CONFIG_PROPERTY"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["properties"]["code"], "https://api.stripe.com/v1/charges")

        refs = [r for r in graph["relationships"] if r["type"] == "REFERENCES_CONFIG"]
        self.assertEqual(len(refs), 1)
        by_qn = self._nodes_by_qn(graph)
        self.assertEqual(refs[0]["sourceId"], by_qn["com.example.PaymentService.charge"]["id"])

    def test_http_dependency_detected_from_any_annotation_with_url_attribute(self):
        """No annotation-name allowlist: a custom, non-Feign annotation with a
        literal URL attribute is captured the same way @FeignClient would be."""
        _write(self.repo, "src/com/example/LegacyClient.java", """
package com.example;
@RemoteService(endpoint = "http://legacy-service:8080")
public interface LegacyClient {
    String ping();
}
""")
        graph = extract_repo(self.repo).to_json()
        props = [n for n in graph["nodes"] if n["type"] == "CONFIG_PROPERTY"]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["properties"]["code"], "http://legacy-service:8080")

    def test_placeholder_only_annotation_attribute_not_captured_as_http_dependency(self):
        """A non-literal placeholder (${...}) isn't a URL literal -- accepted
        trade-off, not a bug (see http_url_detection.py's docstring)."""
        _write(self.repo, "src/com/example/PlaceholderClient.java", """
package com.example;
@RemoteService(name = "inventory-service", endpoint = "${inventory.service.url}")
public interface PlaceholderClient {
    String ping();
}
""")
        graph = extract_repo(self.repo).to_json()
        props = [n for n in graph["nodes"] if n["type"] == "CONFIG_PROPERTY"]
        self.assertEqual(props, [])

    def test_schema_accepts_method_calling_free_function(self):
        """Regression guard for the PHP increment's additive CALLS combo --
        must not regress Java, which never emits this combo itself but must
        not error if graph_store.py's schema is malformed."""
        import tempfile
        import graph_store

        with tempfile.TemporaryDirectory() as tmp:
            store = graph_store.LadybugGraphStore(f"{tmp}/test-db")
            store.init_schema()
            store.execute(
                "CREATE (m:Method {id: 'm1', name: 'm', qualifiedName: 'm', "
                "external: false, language: 'java', kind: 'Method', normKind: 'x'})"
            )
            store.execute(
                "CREATE (f:Function {id: 'f1', name: 'f', qualifiedName: 'f', "
                "external: false, language: 'php', kind: 'Function', normKind: 'x'})"
            )
            store.execute(
                "MATCH (m:Method {id: 'm1'}), (f:Function {id: 'f1'}) "
                "CREATE (m)-[:CALLS {confidence: 0.9, reason: 'import-resolved', "
                "language: 'php', kind: 'CALLS', normKind: 'x'}]->(f)"
            )
            result = store.execute("MATCH (a:Method)-[c:CALLS]->(b:Function) RETURN count(c) AS c")
            self.assertEqual(result.get_next()[0], 1)


if __name__ == "__main__":
    unittest.main()
