"""Confirms tree-sitter-php parses real PHP without errors and that the
specific node-type/field names php/extractor.py and php/calls.py depend on
still exist in the installed grammar version -- a canary for grammar
version bumps, mirroring no existing file 1:1 but serving the same role
test_extractor.py's basic parse tests do for Java."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tree_sitter import Language, Parser  # noqa: E402
import tree_sitter_php as tsphp  # noqa: E402

PHP_LANGUAGE = Language(tsphp.language_php())


class PhpGrammarSmokeTests(unittest.TestCase):
    def _parse(self, src: str):
        parser = Parser(PHP_LANGUAGE)
        return parser.parse(src.encode("utf8"))

    def test_parses_without_error(self):
        tree = self._parse("<?php\nclass Foo {}\n")
        self.assertFalse(tree.root_node.has_error)

    def test_expected_node_types_present(self):
        tree = self._parse(
            "<?php\n"
            "namespace App;\n"
            "interface I {}\n"
            "trait T {}\n"
            "class C implements I {\n"
            "    use T;\n"
            "    private int $x;\n"
            "    public function m(string $a): bool { return true; }\n"
            "    public function __construct(private int $y) {}\n"
            "    public static function make(): self { return new self(); }\n"
            "    public function other() { return static::make(); }\n"
            "}\n"
            "function helper($x) { return $x; }\n"
            "helper(1);\n"
            "new C();\n"
            "$name = \"hello\";\n"
            "Route::get('/x', [C::class, 'm']);\n"
            "Route::middleware('auth')->group(function () {\n"
            "    Route::post('/y', 'C@m');\n"
            "});\n"
        )
        seen: set[str] = set()

        def walk(n):
            seen.add(n.type)
            for c in n.children:
                walk(c)

        walk(tree.root_node)
        for expected in [
            "namespace_definition", "interface_declaration", "trait_declaration",
            "class_declaration", "use_declaration", "property_declaration",
            "method_declaration", "property_promotion_parameter", "simple_parameter",
            "scoped_call_expression", "member_call_expression", "function_call_expression",
            "object_creation_expression", "class_constant_access_expression",
            "array_creation_expression", "anonymous_function", "relative_scope",
            "string", "encapsed_string",
        ]:
            self.assertIn(expected, seen, f"grammar no longer emits {expected!r}")


if __name__ == "__main__":
    unittest.main()
