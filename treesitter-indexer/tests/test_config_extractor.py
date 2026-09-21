"""Unit tests for config/extractor.py against small in-repo config files."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_model import Graph  # noqa: E402
from config.extractor import extract_config, link_config_usages  # noqa: E402


def _write(root: str, rel_path: str, content: str) -> None:
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


class ConfigExtractorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _props_by_key(self, graph):
        return {n["name"]: n for n in graph.nodes.values() if n["type"] == "CONFIG_PROPERTY"}

    def test_yaml_flattening_and_multi_document_profiles(self):
        _write(self.repo, "src/main/resources/application.yml", """
server:
  port: 8080
inventory:
  tags:
    - a
    - b
---
server:
  port: 9090
""")
        graph = Graph()
        extract_config(self.repo, graph)
        props = [n for n in graph.nodes.values() if n["type"] == "CONFIG_PROPERTY"]
        keys = {n["qualifiedName"] for n in props}

        self.assertIn("src/main/resources/application.yml[doc0]#server.port", keys)
        self.assertIn("src/main/resources/application.yml[doc1]#server.port", keys)
        by_qn = {n["qualifiedName"]: n for n in props}
        self.assertEqual(by_qn["src/main/resources/application.yml[doc0]#server.port"]["properties"]["code"], "8080")
        self.assertEqual(by_qn["src/main/resources/application.yml[doc1]#server.port"]["properties"]["code"], "9090")
        self.assertEqual(
            by_qn["src/main/resources/application.yml[doc0]#inventory.tags"]["properties"]["code"], "a, b"
        )

        file_nodes = [n for n in graph.nodes.values() if n["type"] == "CONFIG_FILE"]
        self.assertEqual(len(file_nodes), 1)
        contains = [r for r in graph.relationships if r["type"] == "CONTAINS" and r["sourceId"] == file_nodes[0]["id"]]
        self.assertEqual(len(contains), len(props))

    def test_json_flattening(self):
        _write(self.repo, "src/main/resources/settings.json", """
{
  "spring": {"datasource": {"url": "jdbc:postgresql://x/db"}},
  "flags": ["a", "b"]
}
""")
        graph = Graph()
        extract_config(self.repo, graph)
        props = self._props_by_key(graph)
        self.assertEqual(props["spring.datasource.url"]["properties"]["code"], "jdbc:postgresql://x/db")
        self.assertEqual(props["flags"]["properties"]["code"], "a, b")

    def test_properties_file(self):
        _write(self.repo, "src/main/resources/app.properties", """
# a comment
server.port=8080
spring.datasource.url=jdbc:postgresql://x/db
""")
        graph = Graph()
        extract_config(self.repo, graph)
        props = self._props_by_key(graph)
        self.assertEqual(props["server.port"]["properties"]["code"], "8080")
        self.assertEqual(props["spring.datasource.url"]["properties"]["code"], "jdbc:postgresql://x/db")

    def test_src_test_resources_excluded(self):
        _write(self.repo, "src/test/resources/application-test.yml", "server:\n  port: 1234\n")
        graph = Graph()
        extract_config(self.repo, graph)
        self.assertEqual(len([n for n in graph.nodes.values() if n["type"] == "CONFIG_PROPERTY"]), 0)

    def test_link_config_usages_matches_value_annotation(self):
        _write(self.repo, "src/main/resources/application.yml", "inventory:\n  page-size: 20\n")
        graph = Graph()
        extract_config(self.repo, graph)

        # Simulate what java/extractor.py would have already added: a Field
        # node with a @Value("${inventory.page-size:10}") HAS_ANNOTATION edge.
        graph.add_node("field:1", "FIELD", "pageSize", "com.example.Foo.pageSize", external=False)
        graph.add_node("annotation:Value", "ANNOTATION_TYPE", "Value", "Value", external=True)
        graph.add_rel("field:1", "annotation:Value", "HAS_ANNOTATION", value="${inventory.page-size:10}")

        link_config_usages(graph)

        refs = [r for r in graph.relationships if r["type"] == "REFERENCES_CONFIG"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["sourceId"], "field:1")
        target = graph.nodes[refs[0]["targetId"]]
        self.assertEqual(target["name"], "inventory.page-size")


if __name__ == "__main__":
    unittest.main()
