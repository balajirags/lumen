"""
Decorator use-site signal extraction for the Python extractor.

A decorator use-site (`@expr` or `@expr(args)`) is captured generically as one
or more (name, value) pairs, mirroring how java/extractor.py's
_annotation_name_and_value collapses a Java annotation to one name + one
fallback-first value. Two signals are extracted, independently, from the same
use-site:

  1. The primary signal: the decorator's own dotted name (e.g. "app.route",
     "require_login", "dataclass") + its first positional string-literal
     argument, if any (e.g. "/x"). This alone is enough for FastAPI-style
     decorators (`@app.get("/x")`) to resolve as an HTTP entry point through
     postprocess/http_routes.py's resolve_route() with ZERO extra mechanism:
     "app.get" tokenizes to a "get" verb token the same way Micronaut's "Get"
     does.

  2. A verb signal, ONLY when this decorator use-site has a `methods=`/
     `method=`-shaped keyword argument (matched generically by substring, not
     tied to Flask specifically) whose value contains a recognized HTTP verb
     literal (e.g. `methods=["GET", "POST"]`). This is Flask's specific
     mechanism for putting the verb outside the decorator's own name --
     resolve_route() itself is never touched for this: instead, this use-site
     is represented as if it were TWO co-located annotations (like JAX-RS's
     separate @GET + @Path), one carrying the path, one carrying the verb,
     exactly the shape resolve_route() already knows how to combine.

Deliberately generalizes past Flask by construction: any decorator-based
framework whose call has a kwarg name containing "method" and a recognized
verb literal gets the same treatment, without a Flask-specific name check.
"""

from __future__ import annotations

from tree_sitter import Node

from postprocess.http_routes import HTTP_VERBS


def _text(src: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node: Node, src: bytes) -> str | None:
    """Returns the unquoted content of a `string` node, or None if `node`
    isn't a string literal at all."""
    if node.type != "string":
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return _text(src, content) if content is not None else ""


def _decorator_expr(decorator_node: Node) -> Node | None:
    """A `decorator` node's children are ['@', <expr>] -- <expr> is an
    identifier (@dataclass), an attribute (@app), or a call
    (@app.route(...), @require_login())."""
    named = decorator_node.named_children
    return named[0] if named else None


def _call_target_text(call_node: Node, src: bytes) -> str:
    return _text(src, call_node.child_by_field_name("function"))


def _first_positional_string(call_node: Node, src: bytes) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for arg in args.named_children:
        if arg.type == "keyword_argument":
            continue
        value = _string_literal_text(arg, src)
        if value is not None:
            return value
        return None  # first positional arg isn't a string literal -- stop
    return None


def _verb_literals_in_value(value_node: Node, src: bytes) -> list[str]:
    """Collects every string-literal value inside a methods=/method=-shaped
    kwarg's value, whether it's a bare string or a list of strings, and
    returns only the ones that normalize to a recognized HTTP verb."""
    literals: list[str] = []
    if value_node.type == "string":
        text = _string_literal_text(value_node, src)
        if text is not None:
            literals.append(text)
    elif value_node.type in ("list", "tuple", "set"):
        for item in value_node.named_children:
            text = _string_literal_text(item, src)
            if text is not None:
                literals.append(text)
    return [lit.upper() for lit in literals if lit.lower() in HTTP_VERBS]


def _verb_signals(call_node: Node, src: bytes) -> list[str]:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return []
    verbs: list[str] = []
    for arg in args.named_children:
        if arg.type != "keyword_argument":
            continue
        kw_name = _text(src, arg.child_by_field_name("name")).lower()
        if "method" not in kw_name:
            continue
        value_node = arg.child_by_field_name("value")
        if value_node is not None:
            verbs.extend(_verb_literals_in_value(value_node, src))
    return verbs


def extract_decorator_signals(decorator_node: Node, src: bytes) -> list[tuple[str, str | None]]:
    """Returns [(name, value), ...] to emit as separate Decorator use-sites
    (one DECORATES edge each) for a single `@...` decorator -- normally just
    one (name, value) pair, plus one extra (VERB, None) pair per recognized
    HTTP verb literal found in a methods=/method=-shaped kwarg."""
    expr = _decorator_expr(decorator_node)
    if expr is None:
        return []

    if expr.type == "call":
        name = _call_target_text(expr, src)
        value = _first_positional_string(expr, src)
        signals = [(name, value)]
        for verb in _verb_signals(expr, src):
            signals.append((verb, None))
        return signals

    # Bare decorator, no call: @dataclass, @staticmethod, @app.some_attr, ...
    return [(_text(src, expr), None)]
