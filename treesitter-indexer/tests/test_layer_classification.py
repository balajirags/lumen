"""
Unit tests proving postprocess/layer_classification.py's architectural-layer
detection is generic -- exercised against Micronaut, JAX-RS-style package
naming, and a made-up custom annotation, alongside the original Spring case.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from postprocess.layer_classification import layer_from_annotation, layer_from_qualified_name  # noqa: E402


class LayerClassificationTests(unittest.TestCase):
    def test_layer_from_annotation_spring_style(self):
        self.assertEqual(layer_from_annotation("RestController"), "Controller")
        self.assertEqual(layer_from_annotation("Service"), "Service")
        self.assertEqual(layer_from_annotation("Repository"), "Repository")

    def test_layer_from_annotation_micronaut_style(self):
        # Micronaut reuses the exact word "Controller" -- matches the same
        # generic rule, no Micronaut-specific code needed.
        self.assertEqual(layer_from_annotation("Controller"), "Controller")

    def test_layer_from_annotation_custom_framework(self):
        # A framework this project has never heard of, using its own
        # compound annotation name -- must still resolve via the shared word.
        self.assertEqual(layer_from_annotation("ApiController"), "Controller")
        self.assertEqual(layer_from_annotation("DataRepository"), "Repository")

    def test_layer_from_annotation_jpa_entity_is_model(self):
        # @Entity was never in the old hardcoded SPRING_LAYER map at all --
        # this is a genuine improvement, not just a refactor.
        self.assertEqual(layer_from_annotation("Entity"), "Model")

    def test_layer_from_annotation_unrelated_returns_none(self):
        self.assertIsNone(layer_from_annotation("Override"))
        self.assertIsNone(layer_from_annotation("Deprecated"))

    def test_layer_from_qualified_name_package_convention(self):
        # JAX-RS/plain Java apps with no stereotype annotations at all still
        # get a real layer label from package-by-layer naming.
        self.assertEqual(layer_from_qualified_name("com.example.controller.ItemsResource"), "Controller")
        self.assertEqual(layer_from_qualified_name("com.example.service.ItemService"), "Service")
        self.assertEqual(layer_from_qualified_name("com.example.repository.ItemRepository"), "Repository")

    def test_layer_from_qualified_name_jaxrs_resource_convention(self):
        # JAX-RS's own terminology is "resource", not "controller" -- found
        # by testing against a real JAX-RS-style fixture.
        self.assertEqual(layer_from_qualified_name("com.example.resource.ItemsResource"), "Controller")

    def test_layer_from_qualified_name_dao_convention(self):
        self.assertEqual(layer_from_qualified_name("com.example.dao.ItemDao"), "Repository")

    def test_layer_from_qualified_name_model_conventions(self):
        self.assertEqual(layer_from_qualified_name("com.example.model.Item"), "Model")
        self.assertEqual(layer_from_qualified_name("com.example.entity.Item"), "Model")
        self.assertEqual(layer_from_qualified_name("com.example.dto.ItemDto"), "Model")

    def test_layer_from_qualified_name_no_match_returns_none(self):
        self.assertIsNone(layer_from_qualified_name("com.example.util.StringHelper"))


if __name__ == "__main__":
    unittest.main()
