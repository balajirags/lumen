"""
CALLS edge resolution with confidence tiers for Kotlin, plus Ktor routing
recognition (kept in this file because both need the same RepoIndex/
class-method-lookup machinery -- see module docstring in extractor.py).

Mirrors php/calls.py's tier *shape* exactly (0.95/0.90/0.50), adapted for
Kotlin's one genuinely different semantic (verified empirically, see the
plan's Global Constraints): a BARE identifier call inside a method body
implicitly means "call this class's own method" -- Kotlin has no
`this.`/`self::` requirement the way Java/JS/PHP do. Resolution order for
a bare call `inner()`:

  0.95 "same-file"       -- (a) the ENCLOSING CLASS's own method (the
                             implicit-this case, tried FIRST), or (b) a
                             same-package top-level function.
  0.90 "import-resolved"  -- a same-corpus function in a DIFFERENT package
                             (Kotlin has no relative-import resolution the
                             way JS/PHP do -- this engine does not attempt
                             `import` directive resolution at all, an
                             accepted scope boundary; cross-package calls
                             land here via the same-corpus name index
                             instead of real import tracking); `new
                             ClassName()`-equivalent (a bare
                             `ClassName(...)` call resolving to a
                             Constructor) at the same same-file/cross-file
                             split.
  0.50 "global"           -- same-corpus name index: the called method/
                             function's own NAME (not the receiver)
                             matches exactly one callable anywhere in the
                             parsed corpus.

Deliberately NOT resolved in this increment (explicit, approved scope
boundary, same posture as every other language's `$obj->method()`/
`obj.method()` case): `receiver.method()` for any `receiver` other than
the implicit/explicit enclosing-class case -- falls through to the 0.50
global-name tier by method name only.

Ktor routing recognition does NOT hardcode a receiver variable name
(there IS no receiver at all -- Ktor's `get`/`post`/etc. are top-level
functions called inside a `routing { }` block, resolved the same
tokenizing way as every other call-based routing mechanism via
resolve_call_route()). Since Ktor route registration takes a trailing
LAMBDA (not a separate handler reference the way Express/Laravel do), the
route's HAS_ANNOTATION is attached to the single bare function call found
inside that lambda body, if exactly one exists (see the plan's Task 3
note on this narrower heuristic).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graph_model import Graph
from postprocess.call_routes import resolve_call_route

_ROUTING_BLOCK_CALLEE = "routing"


@dataclass
class RepoIndex:
    internal_classes: set[str] = field(default_factory=set)
    class_file: dict[str, str] = field(default_factory=dict)              # class fqn -> file
    class_table: dict[str, str] = field(default_factory=dict)             # class fqn -> table name
    package_file: dict[str, str] = field(default_factory=dict)            # first file seen declaring this package
    package_functions: dict[str, set[str]] = field(default_factory=dict)  # package -> {funcSimpleName}
    class_methods: dict[str, dict[str, str]] = field(default_factory=dict)  # class/object fqn -> {methodName: methodId}
    function_ids: dict[str, str] = field(default_factory=dict)            # "pkg.func" -> functionId
    function_table_by_id: dict[str, str] = field(default_factory=dict)    # callableId -> table, for SUSPENDS detection
    simple_callable_to_fqns: dict[str, list[str]] = field(default_factory=dict)  # 0.50 tier only


@dataclass
class ImportedName:
    kind: str
    target: str


@dataclass
class CallContext:
    package: str
    caller_class: str | None  # class/object fqn, or None for a top-level function
    caller_file: str


def callable_id(fqn: str) -> str:
    return f"callable:{fqn}"


def constructor_id(class_fqn: str) -> str:
    return f"constructor:{class_fqn}"


def _global_fallback(simple_name: str, index: RepoIndex) -> tuple[str, float, str] | None:
    matches = index.simple_callable_to_fqns.get(simple_name, [])
    if len(matches) == 1:
        return matches[0], 0.50, "global"
    return None


def _resolve_bare_call(name: str, ctx: CallContext, index: RepoIndex) -> tuple[str, float, str] | None:
    """Implicit-this FIRST: the enclosing class/object's own method."""
    if ctx.caller_class:
        target = index.class_methods.get(ctx.caller_class, {}).get(name)
        if target:
            return target, 0.95, "same-file"
    same_pkg = index.package_functions.get(ctx.package, set())
    if name in same_pkg:
        fqn = f"{ctx.package}.{name}" if ctx.package else name
        target = index.function_ids.get(fqn)
        if target:
            return target, 0.95, "same-file"
    for fqn, fid in index.function_ids.items():
        if fqn.rsplit(".", 1)[-1] == name:
            same_file = index.package_file.get(fqn.rsplit(".", 1)[0] if "." in fqn else "") == ctx.caller_file
            return fid, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")
    return _global_fallback(name, index)


def _resolve_new_expression(class_name: str, ctx: CallContext, index: RepoIndex) -> tuple[str, float, str] | None:
    resolved_fqn = None
    same_pkg_fqn = f"{ctx.package}.{class_name}" if ctx.package else class_name
    if same_pkg_fqn in index.internal_classes:
        resolved_fqn = same_pkg_fqn
    else:
        for fqn in index.internal_classes:
            if fqn == class_name or fqn.endswith(f".{class_name}"):
                resolved_fqn = fqn
                break
    if resolved_fqn is None:
        return _global_fallback("<init>", index)
    same_file = index.class_file.get(resolved_fqn) == ctx.caller_file
    return constructor_id(resolved_fqn), (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")


# --- Call-site + Ktor routing recognition (walks one file's full AST) ---

def _text(src: bytes, node) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node, src: bytes) -> str | None:
    if node is None or node.type != "string_literal":
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return _text(src, content) if content is not None else ""


def _positional_args(call_node, src: bytes):
    args_node = next((c for c in call_node.named_children if c.type == "value_arguments"), None)
    if args_node is None:
        return []
    out = []
    for a in args_node.named_children:
        if a.type == "value_argument" and a.named_children:
            out.append(a.named_children[0])
    return out


def _call_callee_name(call_node, src: bytes) -> str | None:
    fn = call_node.named_children[0] if call_node.named_children else None
    if fn is None:
        return None
    if fn.type == "identifier":
        return _text(src, fn)
    if fn.type == "navigation_expression":
        prop = fn.named_children[-1] if fn.named_children else None
        return _text(src, prop) if prop is not None and prop.type == "identifier" else None
    return None


def _trailing_lambda_body(call_node):
    for c in call_node.named_children:
        if c.type == "annotated_lambda":
            lit = next((cc for cc in c.named_children if cc.type == "lambda_literal"), None)
            return lit
        if c.type == "lambda_literal":
            return c
    return None


def _is_routing_block(node, src: bytes) -> bool:
    return node.type == "call_expression" and _call_callee_name(node, src) == _ROUTING_BLOCK_CALLEE


def _find_calls(n):
    if n.type == "call_expression":
        yield n
    for c in n.children:
        yield from _find_calls(c)


def _find_wrapped_route_calls(n):
    """A call-with-parens-and-a-trailing-lambda (`get("/path") { ... }`)
    parses as an OUTER call_expression wrapping an INNER call_expression
    (the bare `get("/path")` part) plus a trailing annotated_lambda --
    verified empirically, a genuinely different (nested) shape than
    `routing { ... }`'s flat identifier+lambda shape (no parens at all).
    Yields (innerCallNode, lambdaBodyNode) pairs."""
    if n.type == "call_expression":
        inner = n.named_children[0] if n.named_children else None
        lam = _trailing_lambda_body(n)
        if inner is not None and inner.type == "call_expression" and lam is not None:
            yield inner, lam
    for c in n.children:
        yield from _find_wrapped_route_calls(c)


def _walk_routing_block(graph: Graph, lambda_body, src: bytes, index: RepoIndex) -> None:
    """Recurses into a `routing { ... }` block's lambda body, recognizing
    `get`/`post`/etc. call-sites via resolve_call_route() and attaching
    HAS_ANNOTATION to the single bare function call found inside each
    route's own trailing lambda (see the plan's Task 3 note)."""
    for call, route_lambda in _find_wrapped_route_calls(lambda_body):
        callee = _call_callee_name(call, src)
        if callee is None:
            continue
        args = _positional_args(call, src)
        path_text = _string_literal_text(args[0], src) if args else None
        route = resolve_call_route(callee, [path_text]) if path_text else None
        if route is None:
            continue
        http_method, http_path = route
        inner_calls = [c for c in _find_calls(route_lambda) if c is not route_lambda]
        bare_calls = [c for c in inner_calls if _call_callee_name(c, src) and
                      (c.named_children[0].type == "identifier" if c.named_children else False)]
        if len(bare_calls) != 1:
            continue
        handler_name = _call_callee_name(bare_calls[0], src)
        target_id = index.function_ids.get(handler_name)
        if target_id is None:
            for fqn, fid in index.function_ids.items():
                if fqn.rsplit(".", 1)[-1] == handler_name:
                    target_id = fid
                    break
        if target_id is None:
            continue
        verb_name = http_method.capitalize() if http_method != "HTTP" else "Mapping"
        ann_id = f"annotationtype:{verb_name}"
        if not graph.has_node(ann_id):
            graph.add_node(ann_id, "AnnotationType", verb_name, verb_name, external=True, language="kotlin")
        graph.add_rel(target_id, ann_id, "HAS_ANNOTATION", value=http_path or "", language="kotlin")


def emit_calls_and_routes_in_file(graph: Graph, finfo, index: RepoIndex) -> None:
    src = finfo.src

    def walk(n, ctx: CallContext, caller_id: str | None):
        if _is_routing_block(n, src):
            lambda_body = _trailing_lambda_body(n)
            if lambda_body is not None:
                _walk_routing_block(graph, lambda_body, src, index)
            return

        if n.type == "call_expression" and caller_id is not None:
            _maybe_emit_calls_edge(graph, n, src, caller_id, ctx, index)

        for child in n.children:
            walk(child, ctx, caller_id)

    # Collect (callableId, bodyNode, callerClassFqn) for every class/object
    # method and top-level function, then walk each body for CALLS.
    callable_bodies: list[tuple[str, object, str | None]] = []
    for cls in finfo.classes:
        body = next((c for c in cls.node.children if c.type == "class_body"), None)
        _collect_container_callables(body, cls.fqn, src, callable_bodies)
    for obj in finfo.objects:
        body = next((c for c in obj.node.children if c.type == "class_body"), None)
        _collect_container_callables(body, obj.fqn, src, callable_bodies)

    for m_id, body_node, caller_class in callable_bodies:
        if body_node is None:
            continue
        ctx = CallContext(finfo.package, caller_class, finfo.file_path)
        walk(body_node, ctx, m_id)

    for fn in finfo.callables:
        f_id = index.function_ids[fn.fqn]
        ctx = CallContext(finfo.package, None, finfo.file_path)
        body = next((c for c in fn.node.children if c.type == "function_body"), None)
        if body is not None:
            walk(body, ctx, f_id)

    # Top-level (script) statements -- e.g. a routes.kt file's
    # `routing { ... }` block called with no enclosing function at all.
    handled_node_ids = {id(cls.node) for cls in finfo.classes} | {id(obj.node) for obj in finfo.objects} | {id(fn.node) for fn in finfo.callables}
    top_ctx = CallContext(finfo.package, None, finfo.file_path)
    for child in finfo.top_level_nodes:
        if id(child) in handled_node_ids:
            continue
        walk(child, top_ctx, None)


def _collect_container_callables(body, container_fqn: str, src: bytes,
                                   out: list[tuple[str, object, str | None]]) -> None:
    if body is None:
        return
    for member in body.named_children:
        if member.type == "function_declaration":
            name_node = member.child_by_field_name("name")
            name = _text(src, name_node)
            m_id = callable_id(f"{container_fqn}.{name}")
            fn_body = member.child_by_field_name("body") or next(
                (c for c in member.children if c.type == "function_body"), None)
            out.append((m_id, fn_body, container_fqn))
        elif member.type == "companion_object":
            comp_fqn = f"{container_fqn}.Companion"
            comp_body = next((c for c in member.children if c.type == "class_body"), None)
            _collect_container_callables(comp_body, comp_fqn, src, out)


def _constructor_shaped_names(index: RepoIndex) -> set[str]:
    return {fqn.rsplit(".", 1)[-1] for fqn in index.internal_classes}


def _table_of_callable_id(index: RepoIndex, callable_id_: str) -> str | None:
    return index.function_table_by_id.get(callable_id_)


def _maybe_emit_calls_edge(graph: Graph, call_node, src: bytes, caller_id: str,
                            ctx: CallContext, index: RepoIndex) -> None:
    from kotlin.extractor import _url_literal_args, _emit_http_dependency

    fn = call_node.named_children[0] if call_node.named_children else None
    result: tuple[str, float, str] | None = None

    if fn is not None and fn.type == "identifier":
        name = _text(src, fn)
        if name in _constructor_shaped_names(index):
            result = _resolve_new_expression(name, ctx, index)
        else:
            result = _resolve_bare_call(name, ctx, index)
    elif fn is not None and fn.type == "navigation_expression":
        obj_node = fn.named_children[0] if fn.named_children else None
        prop_node = fn.named_children[-1] if len(fn.named_children) > 1 else None
        if obj_node is not None and prop_node is not None and prop_node.type == "identifier":
            method_name = _text(src, prop_node)
            if obj_node.type == "this_expression":
                result = _resolve_bare_call(method_name, ctx, index)
            else:
                result = _global_fallback(method_name, index)

    for url in _url_literal_args(call_node, src):
        _emit_http_dependency(graph, caller_id, f"http-call:{caller_id}", url)

    if result is not None:
        target_id, confidence, reason = result
        if not graph.has_node(target_id):
            return
        line = call_node.start_point[0] + 1
        graph.add_rel(caller_id, target_id, "CALLS", lineNumber=line,
                      confidence=confidence, reason=reason, language="kotlin")
        # SUSPENDS: an ordinary CALLS-shaped edge, additionally emitted
        # when BOTH endpoints are SuspendFunction-typed (checked via the
        # already-declared SUSPENDS combo, FROM SuspendFunction TO
        # SuspendFunction) -- see Task 2's schema audit.
        caller_table = _table_of_callable_id(index, caller_id)
        target_table = _table_of_callable_id(index, target_id)
        if caller_table == "SuspendFunction" and target_table == "SuspendFunction":
            graph.add_rel(caller_id, target_id, "SUSPENDS", language="kotlin")
