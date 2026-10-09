"""Confirms tree-sitter-javascript and tree-sitter-typescript parse real
JS/TS/TSX without errors and that the specific node-type/field names
js/extractor.py and js/calls.py depend on still exist in the installed
grammar versions -- a canary for grammar version bumps, mirroring
test_php_grammar_smoke.py's role for PHP."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tree_sitter import Language, Parser  # noqa: E402
import tree_sitter_javascript as tsjs  # noqa: E402
import tree_sitter_typescript as tsts  # noqa: E402

JS_LANGUAGE = Language(tsjs.language())
TS_LANGUAGE = Language(tsts.language_typescript())
TSX_LANGUAGE = Language(tsts.language_tsx())


class JsGrammarSmokeTests(unittest.TestCase):
    def _parse(self, language, src: str):
        parser = Parser(language)
        return parser.parse(src.encode("utf8"))

    def test_js_parses_without_error(self):
        tree = self._parse(JS_LANGUAGE, "class Foo {}\n")
        self.assertFalse(tree.root_node.has_error)

    def test_jsx_parses_in_plain_js_grammar(self):
        tree = self._parse(JS_LANGUAGE, "function Widget() { return <div onClick={() => 1}>{2}</div>; }\n")
        self.assertFalse(tree.root_node.has_error)

    def test_ts_decorators_parse_without_special_flag(self):
        tree = self._parse(TS_LANGUAGE, "class C { @Get('/x') show() {} }\n")
        self.assertFalse(tree.root_node.has_error)

    def test_tsx_parses_without_error(self):
        tree = self._parse(TSX_LANGUAGE, "function Widget(props: Props) { return <div>{props.x}</div>; }\n")
        self.assertFalse(tree.root_node.has_error)

    def test_js_expected_node_types_present(self):
        tree = self._parse(
            JS_LANGUAGE,
            "class Foo extends Bar {\n"
            "  constructor(x) { this.x = x; }\n"
            "  outer() { return this.inner(); }\n"
            "  inner() { return 1; }\n"
            "}\n"
            "function helper(x) { return x; }\n"
            "const add = (a, b) => a + b;\n"
            "app.get('/x', function(req, res) { res.send('ok'); });\n"
            "router.post('/y', (req, res) => { res.send('created'); });\n"
            "import { Foo2 } from './foo2';\n"
            "import Bar2 from 'bar-pkg';\n"
            "export function baz() {}\n"
            "export class Qux {}\n"
        )
        self.assertFalse(tree.root_node.has_error)
        seen: set[str] = set()

        def walk(n):
            seen.add(n.type)
            for c in n.children:
                walk(c)

        walk(tree.root_node)
        for expected in [
            "class_declaration", "method_definition", "function_declaration",
            "arrow_function", "lexical_declaration", "variable_declarator",
            "call_expression", "member_expression", "this",
            "import_statement", "import_clause", "named_imports", "import_specifier",
            "export_statement", "string", "string_fragment", "class_heritage",
        ]:
            self.assertIn(expected, seen, f"grammar no longer emits {expected!r}")

    def test_ts_expected_node_types_present(self):
        tree = self._parse(
            TS_LANGUAGE,
            "interface User {\n"
            "  id: number;\n"
            "  getName(): string;\n"
            "}\n"
            "type UserId = string;\n"
            "const items: Array<User> = [];\n"
            "@Injectable()\n"
            "class OrderController extends Base implements IController {\n"
            "  private repository: OrderRepository;\n"
            "  @Get('/:id')\n"
            "  show() {}\n"
            "}\n"
        )
        self.assertFalse(tree.root_node.has_error)
        seen: set[str] = set()

        def walk(n):
            seen.add(n.type)
            for c in n.children:
                walk(c)

        walk(tree.root_node)
        for expected in [
            "interface_declaration", "property_signature", "method_signature",
            "type_alias_declaration", "generic_type", "type_arguments",
            "decorator", "public_field_definition", "accessibility_modifier",
            "class_heritage", "extends_clause", "implements_clause", "type_identifier",
        ]:
            self.assertIn(expected, seen, f"grammar no longer emits {expected!r}")


if __name__ == "__main__":
    unittest.main()
