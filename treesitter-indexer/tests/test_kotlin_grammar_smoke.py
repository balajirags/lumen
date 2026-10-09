"""Confirms tree-sitter-kotlin parses real Kotlin without errors and that
the specific node-type/field names kotlin/extractor.py and kotlin/calls.py
depend on still exist in the installed grammar version -- a canary for
grammar version bumps, mirroring test_php_grammar_smoke.py's role."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tree_sitter import Language, Parser  # noqa: E402
import tree_sitter_kotlin as tsk  # noqa: E402

KOTLIN_LANGUAGE = Language(tsk.language())


class KotlinGrammarSmokeTests(unittest.TestCase):
    def _parse(self, src: str):
        parser = Parser(KOTLIN_LANGUAGE)
        return parser.parse(src.encode("utf8"))

    def test_parses_without_error(self):
        tree = self._parse("class Foo {\n}\n")
        self.assertFalse(tree.root_node.has_error)

    def test_expected_node_types_present(self):
        tree = self._parse(
            "package com.example\n"
            "\n"
            "@RestController\n"
            "class OrderController(private val repository: OrderRepository) {\n"
            "    @GetMapping(\"/orders/{id}\")\n"
            "    fun show(id: String): String {\n"
            "        return repository.findOrder(id)\n"
            "    }\n"
            "\n"
            "    fun outer() {\n"
            "        inner()\n"
            "    }\n"
            "\n"
            "    fun inner() {\n"
            "    }\n"
            "}\n"
            "\n"
            "data class Order(val id: String, val status: String)\n"
            "\n"
            "sealed class PaymentResult\n"
            "class PaymentSuccess : PaymentResult()\n"
            "data class PaymentFailure(val reason: String) : PaymentResult()\n"
            "\n"
            "object Singleton {\n"
            "    fun doThing() {\n"
            "    }\n"
            "}\n"
            "\n"
            "class WithCompanion {\n"
            "    companion object {\n"
            "        fun create(): WithCompanion = WithCompanion()\n"
            "    }\n"
            "}\n"
            "\n"
            "fun String.shout(): String = this.uppercase()\n"
            "\n"
            "suspend fun fetchData(): String {\n"
            "    return fetchMore()\n"
            "}\n"
            "\n"
            "suspend fun fetchMore(): String = \"x\"\n"
            "\n"
            "class WithDelegate {\n"
            "    val lazyValue: String by lazy { \"computed\" }\n"
            "}\n"
            "\n"
            "interface Helper {\n"
            "    fun help(): String\n"
            "}\n"
            "class DelegatingClass(h: Helper) : Helper by h\n"
        )
        self.assertFalse(tree.root_node.has_error)
        seen: set[str] = set()

        def walk(n):
            seen.add(n.type)
            for c in n.children:
                walk(c)

        walk(tree.root_node)
        for expected in [
            "package_header", "class_declaration", "function_declaration",
            "object_declaration", "companion_object", "primary_constructor",
            "class_parameters", "class_parameter", "class_body", "modifiers",
            "annotation", "constructor_invocation", "class_modifier",
            "function_modifier", "delegation_specifiers", "delegation_specifier",
            "explicit_delegation", "property_declaration", "property_delegate",
            "variable_declaration", "navigation_expression", "call_expression",
            "string_literal", "string_content", "value_arguments", "user_type",
        ]:
            self.assertIn(expected, seen, f"grammar no longer emits {expected!r}")


if __name__ == "__main__":
    unittest.main()
