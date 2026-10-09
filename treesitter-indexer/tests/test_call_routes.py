"""Unit tests for resolve_call_route() -- the generic, call-site-shaped
sibling to http_routes.py's resolve_route(). Exercises it directly with
plain strings/lists, with NO PHP/Laravel involvement at all, proving it's
reusable by any call-based routing DSL (Express, Ktor, ...)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from postprocess.call_routes import resolve_call_route  # noqa: E402


class ResolveCallRouteTests(unittest.TestCase):
    def test_plain_get_route(self):
        self.assertEqual(resolve_call_route("get", ["/orders/{id}", "handler"]), ("GET", "/orders/{id}"))

    def test_post_route(self):
        self.assertEqual(resolve_call_route("post", ["/orders"]), ("POST", "/orders"))

    def test_non_verb_callee_returns_none(self):
        # "middleware"/"group" are the wrapper mechanism, not routes
        # themselves -- the extractor's recognition logic, not this
        # resolver, is responsible for not calling this on them in the
        # first place, but the resolver must still be safe/correct if it
        # ever is.
        self.assertIsNone(resolve_call_route("middleware", ["auth"]))
        self.assertIsNone(resolve_call_route("group", []))

    def test_route_hint_word_with_no_verb_returns_any(self):
        self.assertEqual(resolve_call_route("route", ["/x"]), ("HTTP", "/x"))

    def test_no_path_argument_still_resolves_verb_with_none_path(self):
        self.assertEqual(resolve_call_route("get", []), ("GET", None))

    def test_non_string_first_arg_is_ignored_for_path(self):
        self.assertEqual(resolve_call_route("get", [None, "handler"]), ("GET", None))

    def test_reusable_for_a_made_up_framework_shape(self):
        """Proves generality -- Ktor's `get(path) { ... }` or Express's
        `router.get(path, handler)` both reduce to the same (verb, [path,
        ...]) shape this function already handles, with zero changes."""
        self.assertEqual(resolve_call_route("delete", ["/items/{id}"]), ("DELETE", "/items/{id}"))

    def test_laravel_any_and_match_register_as_http_verb(self):
        """Route::any()/Route::match() are explicitly in-scope per the
        design spec -- both must resolve (as the generic "any verb" HTTP
        marker), not silently return None."""
        self.assertEqual(resolve_call_route("any", ["/x"]), ("HTTP", "/x"))
        self.assertEqual(resolve_call_route("match", ["/x"]), ("HTTP", "/x"))


if __name__ == "__main__":
    unittest.main()
