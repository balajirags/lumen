"""
Unit tests proving postprocess/http_routes.py's entry-point detection is
generic -- deliberately exercised against non-Spring annotation names
(JAX-RS, Micronaut, a made-up custom framework) to prove no framework name is
hardcoded, alongside the original Spring case to prove no regression.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from postprocess.http_routes import annotation_http_verb, looks_like_route_path, resolve_route  # noqa: E402


class HttpRoutesTests(unittest.TestCase):
    def test_annotation_http_verb_spring_style(self):
        self.assertEqual(annotation_http_verb("GetMapping"), "get")
        self.assertEqual(annotation_http_verb("PostMapping"), "post")
        self.assertEqual(annotation_http_verb("RequestMapping"), "any")

    def test_annotation_http_verb_micronaut_style(self):
        self.assertEqual(annotation_http_verb("Get"), "get")
        self.assertEqual(annotation_http_verb("Post"), "post")

    def test_annotation_http_verb_jaxrs_style(self):
        self.assertEqual(annotation_http_verb("GET"), "get")
        self.assertEqual(annotation_http_verb("POST"), "post")

    def test_annotation_http_verb_rejects_unrelated_names(self):
        # Lombok's @Getter starts with "get" but isn't a route annotation --
        # must not false-positive just because of a prefix match.
        self.assertIsNone(annotation_http_verb("Getter"))
        self.assertIsNone(annotation_http_verb("Override"))
        self.assertIsNone(annotation_http_verb("Entity"))

    def test_looks_like_route_path(self):
        self.assertTrue(looks_like_route_path("/api/items"))
        self.assertFalse(looks_like_route_path("items"))
        self.assertFalse(looks_like_route_path("http://example.com"))

    def test_resolve_route_spring_single_annotation(self):
        self.assertEqual(resolve_route({"GetMapping": "/items"}), ("GET", "/items"))

    def test_resolve_route_jaxrs_two_annotations_combine_verb_and_path(self):
        # JAX-RS: @GET and @Path("/items") are two separate annotations on
        # the same method -- proves signals combine across annotations, not
        # just within one, without any JAX-RS-specific code.
        self.assertEqual(resolve_route({"GET": None, "Path": "/items"}), ("GET", "/items"))

    def test_resolve_route_micronaut_style(self):
        self.assertEqual(resolve_route({"Get": "/items"}), ("GET", "/items"))

    def test_resolve_route_custom_framework_annotation(self):
        # A framework this project has never heard of, using its own
        # convention (verb-named annotation with a path attribute) -- must
        # still resolve.
        self.assertEqual(
            resolve_route({"HttpDelete": None, "RoutePath": "/widgets/{id}"}),
            ("DELETE", "/widgets/{id}"),
        )

    def test_resolve_route_generic_request_mapping_defaults_to_http_method(self):
        # Matches the pre-existing hardcoded behavior's limitation: a bare
        # @RequestMapping("/x") has no verb of its own, so method defaults to
        # "HTTP" -- not a regression, see http_routes.py's resolve_route docstring.
        self.assertEqual(resolve_route({"RequestMapping": "/x"}), ("HTTP", "/x"))

    def test_resolve_route_non_route_annotations_return_none(self):
        self.assertIsNone(resolve_route({"Override": None, "Deprecated": None}))

    def test_resolve_route_empty_annotations_return_none(self):
        self.assertIsNone(resolve_route({}))


if __name__ == "__main__":
    unittest.main()
