"""
CALLS edge resolution with confidence tiers for PHP, plus Laravel routing
call-site recognition (kept in this file because both need the same
RepoIndex/class-method-lookup machinery -- see module docstring in
extractor.py for why).

Mirrors python/calls.py's tier *shape* exactly (0.95/0.90/0.50), but the
old PHP engine's actual resolved cases differ from Python's, so the
PER-CASE logic is PHP-specific, verified against the old
indexer/parsers/php/parse.php by direct read during planning:

  0.95 "same-file"       -- `$this->method()` / `self::method()` /
                             `static::method()`, resolved against the
                             CURRENT class's own methods only (no
                             inheritance walk).
  0.90 "import-resolved"  -- `ClassName::method()` (static call) or a bare
                             `function_name()` call, resolved via the
                             same-corpus class/function index -- "same-file"
                             instead if the resolved target's file matches
                             the caller's file, mirroring python/calls.py's
                             own same-file-vs-cross-file distinction;
                             `new ClassName()` resolves to that class's
                             Constructor node (PHP's "new" IS a call to
                             __construct) at the same same-file/cross-file
                             split.
  0.50 "global"           -- does not exist in the old engine. Same-corpus
                             name index: the called method/function/
                             constructor's own NAME (not the receiver)
                             matches exactly one callable anywhere in the
                             parsed corpus.

Deliberately NOT resolved in this increment (explicit, approved scope
boundary -- see the design spec): `$obj->method()` for any `$obj` other
than `$this` -- the old engine never resolved this either, and this
increment only adds the 0.50 fallback on top of the old engine's exact two
cases, not a new type-inference tier. Such calls fall through to the 0.50
global-name tier (by method name only) rather than getting a real
same-file/import-resolved tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graph_model import Graph
from postprocess.call_routes import resolve_call_route


@dataclass
class RepoIndex:
    internal_classes: set[str] = field(default_factory=set)
    class_file: dict[str, str] = field(default_factory=dict)              # class fqn -> file
    namespace_file: dict[str, str] = field(default_factory=dict)          # first file seen declaring this namespace
    namespace_functions: dict[str, set[str]] = field(default_factory=dict)  # namespace -> {funcSimpleName}
    class_methods: dict[str, dict[str, str]] = field(default_factory=dict)   # class fqn -> {methodName: methodId}
    function_ids: dict[str, str] = field(default_factory=dict)            # "Ns\\func" -> functionId
    simple_callable_to_fqns: dict[str, list[str]] = field(default_factory=dict)  # 0.50 tier only

    def resolve_function_in_namespace(self, namespace: str, simple_name: str) -> str | None:
        if simple_name in self.namespace_functions.get(namespace, ()):
            fqn = f"{namespace}\\{simple_name}" if namespace else simple_name
            return self.function_ids.get(fqn)
        return None


@dataclass
class ImportedName:
    kind: str  # "class" (only kind resolved in this increment)
    target: str  # FQCN


@dataclass
class MethodCallContext:
    caller_namespace: str
    caller_class: str | None  # class fqn, or None for a top-level function/script
    caller_file: str
    imports: dict[str, ImportedName]


def method_id(class_fqn: str, method_name: str) -> str:
    return f"method:{class_fqn}#{method_name}"


def constructor_id(class_fqn: str) -> str:
    return f"constructor:{class_fqn}"


def function_id(fqn: str) -> str:
    return f"function:{fqn}"


def _global_fallback(simple_name: str, index: RepoIndex) -> tuple[str, float, str] | None:
    matches = index.simple_callable_to_fqns.get(simple_name, [])
    if len(matches) == 1:
        return matches[0], 0.50, "global"
    return None


def resolve_call_target(
    shape: str,                 # "this" | "self_static" | "static_class" | "function" | "new"
    class_or_func_name: str | None,  # the class name for self_static/static_class/new, None for "this"/"function"
    method_or_func_name: str,
    ctx: MethodCallContext,
    index: "RepoIndex",
) -> tuple[str, float, str] | None:
    """Returns (target_id, confidence, reason) or None if unresolvable."""

    if shape == "this":
        if ctx.caller_class:
            target = index.class_methods.get(ctx.caller_class, {}).get(method_or_func_name)
            if target:
                return target, 0.95, "same-file"
        return _global_fallback(method_or_func_name, index)

    if shape == "self_static":
        if ctx.caller_class:
            target = index.class_methods.get(ctx.caller_class, {}).get(method_or_func_name)
            if target:
                return target, 0.95, "same-file"
        return _global_fallback(method_or_func_name, index)

    if shape in ("static_class", "new"):
        resolved_fqn = _resolve_simple_class(class_or_func_name or "", ctx, index)
        if resolved_fqn:
            if shape == "new":
                target = constructor_id(resolved_fqn) if _has_constructor(resolved_fqn, index) else None
            else:
                target = index.class_methods.get(resolved_fqn, {}).get(method_or_func_name)
            if target:
                same_file = index.class_file.get(resolved_fqn) == ctx.caller_file
                return target, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")
        return _global_fallback(method_or_func_name if shape == "static_class" else "__construct", index)

    if shape == "function":
        target = index.resolve_function_in_namespace(ctx.caller_namespace, method_or_func_name)
        if target:
            same_file = index.namespace_file.get(ctx.caller_namespace) == ctx.caller_file
            return target, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")
        return _global_fallback(method_or_func_name, index)

    return None


def _has_constructor(class_fqn: str, index: RepoIndex) -> bool:
    # Constructors are intentionally excluded from class_methods (see
    # _emit_method_or_constructor in extractor.py); this project doesn't
    # track them in RepoIndex separately, so "does a constructor exist" is
    # approximated as "always assume yes if the class itself is internal" --
    # a harmless over-approximation: if the class has no explicit
    # __construct, PHP implicitly has a default one anyway, so pointing a
    # `new` CALLS edge at a constructor id that the extractor never created
    # as a node is the one accepted gap here (the CALLS CREATE will no-op,
    # same documented behavior as any id that doesn't exist -- matches the
    # old engine's own "only emit if target exists" posture for CALLS).
    return class_fqn in index.internal_classes


def _resolve_simple_class(simple_or_qualified: str, ctx: MethodCallContext, index: RepoIndex) -> str | None:
    if simple_or_qualified in ("self", "static"):
        return ctx.caller_class
    if simple_or_qualified.startswith("\\"):
        candidate = simple_or_qualified[1:]
        return candidate if candidate in index.internal_classes else None
    same_ns = f"{ctx.caller_namespace}\\{simple_or_qualified}" if ctx.caller_namespace else simple_or_qualified
    if same_ns in index.internal_classes:
        return same_ns
    imported = ctx.imports.get(simple_or_qualified)
    if imported and imported.kind == "class" and imported.target in index.internal_classes:
        return imported.target
    return None


# --- Call-site + routing recognition (walks one file's full AST) ---

def _text(src: bytes, node) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node, src: bytes) -> str | None:
    if node is None or node.type not in ("string", "encapsed_string"):
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return _text(src, content) if content is not None else ""


def _positional_args(call_or_arguments_node, src: bytes):
    """Returns the `arguments` node's positional `argument` children's
    underlying expression nodes (unwraps the `argument` wrapper -- verified
    empirically that `argument` has exactly one named child, the real
    expression, with no field name)."""
    args_node = call_or_arguments_node if call_or_arguments_node.type == "arguments" else \
        next((c for c in call_or_arguments_node.children if c.type == "arguments"), None)
    if args_node is None:
        return []
    out = []
    for a in args_node.named_children:
        if a.type == "argument" and a.named_children:
            out.append(a.named_children[0])
    return out


def _route_verb_args_as_values(arg_nodes, src: bytes) -> list:
    return [_string_literal_text(n, src) for n in arg_nodes]


def _resolve_handler(handler_node, caller_namespace: str, imports: dict, index: RepoIndex, src: bytes) -> str | None:
    """[Controller::class, 'method'] array form, or 'Controller@method'
    string form -- both resolve to a target Method id via the SAME
    same-corpus class/method lookup resolve_call_target's static_class case
    uses, not a separate mechanism."""
    if handler_node.type == "array_creation_expression":
        elems = [c for c in handler_node.named_children if c.type == "array_element_initializer"]
        if len(elems) != 2:
            return None
        class_part, method_part = elems[0], elems[1]
        if class_part.named_children and class_part.named_children[0].type == "class_constant_access_expression":
            cls_access = class_part.named_children[0]
            cls_name_node = next((c for c in cls_access.named_children if c.type == "name"), None)
            cls_name = _text(src, cls_name_node)
        else:
            return None
        # method_part is still the array_element_initializer wrapper, not the
        # string itself -- unwrap it the same way class_part was unwrapped
        # above (verified empirically: both element slots of an
        # array_creation_expression wrap their value in this node type).
        method_value = method_part.named_children[0] if method_part.named_children else None
        method_name = _string_literal_text(method_value, src)
        if not method_name:
            return None
        fake_ctx = MethodCallContext(caller_namespace, None, "", imports)
        resolved_fqn = _resolve_simple_class(cls_name, fake_ctx, index)
        if resolved_fqn:
            return index.class_methods.get(resolved_fqn, {}).get(method_name)
        return None

    text = _string_literal_text(handler_node, src)
    if text and "@" in text:
        cls_name, method_name = text.split("@", 1)
        fake_ctx = MethodCallContext(caller_namespace, None, "", imports)
        resolved_fqn = _resolve_simple_class(cls_name, fake_ctx, index)
        if resolved_fqn:
            return index.class_methods.get(resolved_fqn, {}).get(method_name)
    return None


def _is_route_facade_call(node, src: bytes) -> tuple[str, list, object] | None:
    """Returns (verbOrWrapperName, positionalArgNodes, argsHolderNode) if
    `node` is a `Route::<name>(...)` static call, else None. Recognizing
    the `Route` facade by its literal class name IS Laravel-specific --
    this is the one deliberate, documented exception to the "never hardcode
    a framework name" rule, scoped to exactly this recognition step (see
    postprocess/call_routes.py's module docstring)."""
    if node.type != "scoped_call_expression":
        return None
    scope = node.child_by_field_name("scope")
    if scope is None or scope.type != "name" or _text(src, scope) != "Route":
        return None
    name_node = node.child_by_field_name("name")
    args = node.child_by_field_name("arguments")
    if name_node is None or args is None:
        return None
    return _text(src, name_node), _positional_args(args, src), args


def _walk_group_body(graph: Graph, body_node, middleware: str | None, caller_namespace: str,
                      imports: dict, index: RepoIndex, src: bytes) -> None:
    """Recurses into a Route::middleware(...)->group(closure)'s body,
    applying `middleware` to every nested Route::<verb>(...) call found, and
    recursing again for nested groups."""

    def walk(n):
        facade_call = _is_route_facade_call(n, src)
        if facade_call is not None:
            verb_name, arg_nodes, _args_holder = facade_call
            values = _route_verb_args_as_values(arg_nodes, src)
            route = resolve_call_route(verb_name, values)
            if route is not None and len(arg_nodes) >= 2:
                http_method, http_path = route
                target_id = _resolve_handler(arg_nodes[1], caller_namespace, imports, index, src)
                if target_id:
                    from php.extractor import _emit_route_annotation
                    _emit_route_annotation(graph, target_id, http_method, http_path, middleware)
            return  # do not descend further into this call's own arguments
        member_call = _member_call_middleware_group(n, src)
        if member_call is not None:
            nested_middleware, nested_body = member_call
            _walk_group_body(graph, nested_body, nested_middleware or middleware, caller_namespace,
                              imports, index, src)
            return
        for child in n.children:
            walk(child)

    walk(body_node)


def _member_call_middleware_group(node, src: bytes) -> tuple[str | None, object] | None:
    """Returns (middlewareNameOrNone, closureBodyNode) if `node` is a
    `Route::middleware('name')->group(function () { ... })` call, else
    None. Scope boundary (documented, approved): only a single, direct
    `Route::middleware(X)->group(...)` chain is recognized -- NOT arbitrary
    `->middleware(a)->middleware(b)->group(...)` chains."""
    if node.type != "member_call_expression":
        return None
    name_node = node.child_by_field_name("name")
    if name_node is None or _text(src, name_node) != "group":
        return None
    obj = node.child_by_field_name("object")
    args = node.child_by_field_name("arguments")
    if obj is None or args is None:
        return None
    closure = next((a for a in _positional_args(args, src) if a.type == "anonymous_function"), None)
    if closure is None:
        return None
    closure_body = closure.child_by_field_name("body")
    if closure_body is None:
        return None

    middleware_name = None
    if obj.type == "scoped_call_expression":
        scope = obj.child_by_field_name("scope")
        obj_name = obj.child_by_field_name("name")
        if (scope is not None and scope.type == "name" and _text(src, scope) == "Route"
                and obj_name is not None and _text(src, obj_name) == "middleware"):
            obj_args = obj.child_by_field_name("arguments")
            mw_args = _positional_args(obj_args, src) if obj_args is not None else []
            if mw_args:
                middleware_name = _string_literal_text(mw_args[0], src)
    return middleware_name, closure_body


def emit_calls_and_routes_in_file(graph: Graph, finfo, index: RepoIndex) -> None:
    """Walks one file's full AST (not scoped to inside a method body --
    Laravel route files are top-level procedural statements with no
    enclosing function at all) emitting CALLS edges for every recognized
    call shape, and HAS_ANNOTATION route/middleware edges for every
    recognized `Route::` call-site."""
    src = finfo.src

    def walk(n, ctx: MethodCallContext, caller_id: str | None):
        facade_call = _is_route_facade_call(n, src)
        if facade_call is not None:
            verb_name, arg_nodes, _args_holder = facade_call
            values = _route_verb_args_as_values(arg_nodes, src)
            route = resolve_call_route(verb_name, values)
            if route is not None and len(arg_nodes) >= 2:
                http_method, http_path = route
                target_id = _resolve_handler(arg_nodes[1], finfo.namespace, finfo.imports, index, src)
                if target_id:
                    from php.extractor import _emit_route_annotation
                    _emit_route_annotation(graph, target_id, http_method, http_path, None)
            return

        group_call = _member_call_middleware_group(n, src)
        if group_call is not None:
            middleware, body = group_call
            _walk_group_body(graph, body, middleware, finfo.namespace, finfo.imports, index, src)
            return

        if n.type in ("member_call_expression", "scoped_call_expression", "function_call_expression",
                      "object_creation_expression") and caller_id is not None:
            _maybe_emit_calls_edge(graph, n, src, caller_id, ctx, index)

        for child in n.children:
            walk(child, ctx, caller_id)

    tree_root_imports = finfo.imports
    for cls in finfo.classes:
        for member in (cls.node.child_by_field_name("body").named_children
                        if cls.node.child_by_field_name("body") is not None else []):
            if member.type != "method_declaration":
                continue
            method_name = _text(src, member.child_by_field_name("name"))
            caller_id = (constructor_id(cls.fqn) if method_name == "__construct" else method_id(cls.fqn, method_name))
            body_node = member.child_by_field_name("body")
            ctx = MethodCallContext(finfo.namespace, cls.fqn, finfo.file_path, tree_root_imports)
            if body_node is not None:
                for stmt in body_node.children:
                    walk(stmt, ctx, caller_id)

    for fn in finfo.functions:
        caller_id = function_id(fn.fqn)
        ctx = MethodCallContext(finfo.namespace, None, finfo.file_path, tree_root_imports)
        body_node = fn.node.child_by_field_name("body")
        if body_node is not None:
            for stmt in body_node.children:
                walk(stmt, ctx, caller_id)

    # Top-level (script) statements -- e.g. a routes/web.php file's
    # Route::get(...) calls with no enclosing function at all. caller_id is
    # None here (no CALLS edges are attributed to "the script"), but routing
    # recognition still runs via the same walk() function.
    top_ctx = MethodCallContext(finfo.namespace, None, finfo.file_path, tree_root_imports)
    # Skip re-walking class/function declaration nodes already walked above
    # with a real caller_id -- not just an optimization: without this skip,
    # a routing call-site nested inside a method body (unusual but valid
    # PHP) would be detected and emitted TWICE, once by the per-method walk
    # and once by this top-level pass. Compares by Python object identity
    # (id(), the builtin -- NOT tree-sitter's own unrelated concept of node
    # identity), which is safe here because these are the exact same Node
    # objects _top_level_scan already captured, never re-parsed.
    handled_node_ids = {id(cls.node) for cls in finfo.classes} | {id(fn.node) for fn in finfo.functions}
    for child in _top_level_statement_nodes(finfo):
        if id(child) in handled_node_ids:
            continue
        walk(child, top_ctx, None)


def _top_level_statement_nodes(finfo):
    """Re-parsing is avoided -- extractor.py's _top_level_scan already
    parsed this file once; rather than re-parsing here, extractor.py
    attaches the original tree_root's children onto FileInfo as
    `top_level_nodes`."""
    return finfo.top_level_nodes


def _maybe_emit_calls_edge(graph: Graph, call_node, src: bytes, caller_id: str,
                            ctx: MethodCallContext, index: RepoIndex) -> None:
    from php.extractor import _url_literal_args, _emit_http_dependency

    if call_node.type == "member_call_expression":
        obj = call_node.child_by_field_name("object")
        name_node = call_node.child_by_field_name("name")
        if obj is None or name_node is None:
            return
        method_name = _text(src, name_node)
        if obj.type == "variable_name" and _text(src, obj) == "$this":
            result = resolve_call_target("this", None, method_name, ctx, index)
        else:
            # $obj->method() for any $obj other than $this -- out of scope
            # for a real tier in this increment (see module docstring: no
            # type inference), so it falls through to the 0.50 global-name
            # tier by method name only, same as any other ambiguous call.
            result = _global_fallback(method_name, index)
    elif call_node.type == "scoped_call_expression":
        scope = call_node.child_by_field_name("scope")
        name_node = call_node.child_by_field_name("name")
        if scope is None or name_node is None:
            return
        method_name = _text(src, name_node)
        if scope.type == "relative_scope":
            result = resolve_call_target("self_static", None, method_name, ctx, index)
        elif scope.type == "name":
            class_name = _text(src, scope)
            if class_name == "Route":
                return  # already handled as a routing call, not a CALLS edge
            result = resolve_call_target("static_class", class_name, method_name, ctx, index)
        else:
            return
    elif call_node.type == "function_call_expression":
        fn_node = call_node.child_by_field_name("function")
        if fn_node is None or fn_node.type != "name":
            return
        func_name = _text(src, fn_node)
        result = resolve_call_target("function", None, func_name, ctx, index)
    elif call_node.type == "object_creation_expression":
        name_node = next((c for c in call_node.named_children if c.type == "name"), None)
        if name_node is None:
            return
        class_name = _text(src, name_node)
        result = resolve_call_target("new", class_name, "__construct", ctx, index)
    else:
        return

    for url in _url_literal_args(call_node, src):
        _emit_http_dependency(graph, caller_id, f"http-call:{caller_id}", url)

    if result is not None:
        target_id, confidence, reason = result
        if not graph.has_node(target_id):
            return
        line = call_node.start_point[0] + 1
        graph.add_rel(caller_id, target_id, "CALLS", lineNumber=line,
                      confidence=confidence, reason=reason, language="php")
