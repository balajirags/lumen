"""Runs the real mini-fullstack-service fixture (Java + Python + JS in one repo)
through the same steps parse_all.py runs, and checks the two bugs the unified
switch exists to avoid: Domain duplication and cross-language Workflow bleed."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph_store  # noqa: E402
from languages import LANGUAGES  # noqa: E402
from parse_all import build_merged_graph  # noqa: E402
from postprocess import domain_detector  # noqa: E402

FIXTURE = ROOT.parent / "e2e-test" / "fixtures" / "mini-fullstack-service"
LANGS = ["java", "python", "js"]


def _rows(store, cypher):
    result = store.execute(cypher)
    out = []
    while result.has_next():
        out.append(result.get_next())
    return out


class PolyglotPostprocessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store = graph_store.LadybugGraphStore(os.path.join(cls._tmp.name, "test-db"))
        cls.store.init_schema()
        graph = build_merged_graph(str(FIXTURE), LANGS)
        cls.store.save(graph.to_json())
        cls.workflow_counts = {k: LANGUAGES[k].build_workflows(cls.store) for k in LANGS}
        cls.domain_count = domain_detector.detect_domains(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls._tmp.cleanup()

    def test_each_language_keeps_its_own_tag(self):
        langs = {r[0] for r in _rows(self.store, "MATCH (m:Method) RETURN DISTINCT m.language")}
        self.assertTrue({"java", "python", "js"} <= langs)

    def test_workflows_are_scoped_to_their_own_language(self):
        self.assertGreater(self.workflow_counts["java"], 0)
        self.assertGreater(self.workflow_counts["python"], 0)
        # every WORKFLOW_STEP node must share the language of its Workflow
        mismatched = _rows(
            self.store,
            "MATCH (n:Method)-[s:WORKFLOW_STEP]->(w:Workflow) WHERE n.language <> w.language RETURN n.id")
        mismatched += _rows(
            self.store,
            "MATCH (n:Function)-[s:WORKFLOW_STEP]->(w:Workflow) WHERE n.language <> w.language RETURN n.id")
        self.assertEqual(mismatched, [])

    def test_workflow_totals_match_per_builder_counts(self):
        total = _rows(self.store, "MATCH (w:Workflow) RETURN count(*)")[0][0]
        self.assertEqual(total, sum(self.workflow_counts.values()))

    def test_domains_run_once_with_unique_ids(self):
        ids = [r[0] for r in _rows(self.store, "MATCH (d:Domain) RETURN d.id")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), self.domain_count)

    def test_domains_exist_for_each_language(self):
        langs = {r[0] for r in _rows(self.store, "MATCH (d:Domain) RETURN DISTINCT d.language")}
        self.assertTrue({"java", "python"} <= langs)


if __name__ == "__main__":
    unittest.main()
