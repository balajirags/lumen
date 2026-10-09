"""
CALLS edge resolution with confidence tiers for JS/TS, plus Express/
Fastify/NestJS routing recognition and class-member/decorator pairing
(kept in this file because all three need the same RepoIndex/class-method-
lookup machinery -- see module docstring in extractor.py for why, same
rationale as php/calls.py).

Mirrors php/calls.py's tier *shape* exactly (0.95/0.90/0.50), adapted for
JS semantics -- the old JS engine's actual resolved cases, verified
against indexer/parsers/javascript/parse.js by direct read during
planning:

  0.95 "same-file"       -- `this.method()` (THE FIX -- the old engine
                             built a wrong-shaped target id for this case
                             and the edge was silently dropped by a final
                             id-existence filter), or a bare `name()` call
                             resolving to a top-level callable/class-
                             static-style call in the SAME module.
  0.90 "import-resolved"  -- a bare `name()` call resolving via a relative
                             import (`import { name } from './x'`) to
                             another module in the corpus; `new ClassName()`
                             at the same same-file/cross-file split.
  0.50 "global"           -- does not exist in the old engine. Same-corpus
                             name index: the called method/function's own
                             NAME (not the receiver) matches exactly one
                             callable anywhere in the parsed corpus.

Deliberately NOT resolved in this increment (explicit, approved scope
boundary -- see the design spec, same posture as PHP's $obj->method()):
`obj.method()` for any `obj` other than `this` and other than a KNOWN
internal class name used class-static-style (`ClassName.staticMethod()`)
-- falls through to the 0.50 global-name tier by method name only.

Hook usage (`useXxx()`) is intercepted BEFORE the generic CALLS path and
emits USES_HOOK instead -- a hook call is a real, modeled relationship
type that already exists in the schema, not an ordinary call.

Express/Fastify routing recognition does NOT hardcode a receiver variable
name (unlike PHP's required `Route` facade check) -- Express/Fastify's
receiver is an arbitrary, developer-named variable (`app`, `router`,
`fastify`, ...), so there is no fixed name to anchor on. Instead, a call is
only treated as a route registration if its property name tokenizes to an
HTTP verb/route-hint word AND it has a path-shaped first argument AND a
second argument is present (the handler) -- this two-signal check is what
keeps a `cache.get(key)`-style single-argument call from being
misdetected as a route (see test_map_get_is_not_misdetected_as_a_route).
NestJS decorator routes need no such check: a `@Get(...)`-shaped decorator
is unambiguous by construction, reusing http_routes.py's
`annotation_http_verb()` directly rather than building a new resolver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from graph_model import Graph
from postprocess.call_routes import resolve_call_route
from postprocess.http_routes import annotation_http_verb, looks_like_route_path

_HOOK_CALL_RE = re.compile(r"^use[A-Z]")


@dataclass
class RepoIndex:
    internal_classes: set[str] = field(default_factory=set)
    class_file: dict[str, str] = field(default_factory=dict)                 # class fqn -> file
    module_file: dict[str, str] = field(default_factory=dict)                # module qn -> file
    module_classes: dict[str, dict[str, str]] = field(default_factory=dict)  # module qn -> {className: classId}
    module_callables: dict[str, dict[str, str]] = field(default_factory=dict)  # module qn -> {name: callableId}
    class_methods: dict[str, dict[str, str]] = field(default_factory=dict)   # class fqn -> {methodName: methodId}
    simple_callable_to_fqns: dict[str, list[str]] = field(default_factory=dict)  # 0.50 tier only


@dataclass
class ImportedName:
    target_module: str  # resolved module qn


@dataclass
class CallContext:
    module_qn: str
    caller_class: str | None  # class fqn, or None for a top-level function
    module_file: str
    imports: dict[str, ImportedName]


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
    """A bare `name()` call: same-module top-level callable (same-file), or
    resolved via a relative import to another module (import-resolved)."""
    same_module = index.module_callables.get(ctx.module_qn, {}).get(name)
    if same_module:
        return same_module, 0.95, "same-file"
    imported = ctx.imports.get(name)
    if imported:
        target = index.module_callables.get(imported.target_module, {}).get(name)
        if target:
            same_file = index.module_file.get(imported.target_module) == ctx.module_file
            return target, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")
    return _global_fallback(name, index)


def _resolve_this_method(name: str, ctx: CallContext, index: RepoIndex) -> tuple[str, float, str] | None:
    if ctx.caller_class:
        target = index.class_methods.get(ctx.caller_class, {}).get(name)
        if target:
            return target, 0.95, "same-file"
    return _global_fallback(name, index)


def _resolve_class_static_call(class_name: str, method_name: str, ctx: CallContext, index: RepoIndex) -> tuple[str, float, str] | None:
    """`ClassName.method()` where `ClassName` is a KNOWN internal class --
    same-module or import-resolved, same same-file/cross-file split."""
    target_class_id = index.module_classes.get(ctx.module_qn, {}).get(class_name)
    target_module = ctx.module_qn if target_class_id else None
    if target_class_id is None:
        imported = ctx.imports.get(class_name)
        if imported:
            target_class_id = index.module_classes.get(imported.target_module, {}).get(class_name)
            target_module = imported.target_module
    if target_class_id is None:
        return _global_fallback(method_name, index)
    class_fqn = target_class_id[len("class:"):]
    target = index.class_methods.get(class_fqn, {}).get(method_name)
    if target:
        same_file = index.class_file.get(class_fqn) == ctx.module_file
        return target, (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")
    return _global_fallback(method_name, index)


def _resolve_new_expression(class_name: str, ctx: CallContext, index: RepoIndex) -> tuple[str, float, str] | None:
    target_class_id = index.module_classes.get(ctx.module_qn, {}).get(class_name)
    target_module = ctx.module_qn
    if target_class_id is None:
        imported = ctx.imports.get(class_name)
        if imported:
            target_class_id = index.module_classes.get(imported.target_module, {}).get(class_name)
            target_module = imported.target_module
    if target_class_id is None:
        return _global_fallback("constructor", index)
    class_fqn = target_class_id[len("class:"):]
    if class_fqn not in index.internal_classes:
        return _global_fallback("constructor", index)
    same_file = index.class_file.get(class_fqn) == ctx.module_file
    return constructor_id(class_fqn), (0.95 if same_file else 0.90), ("same-file" if same_file else "import-resolved")


# --- Class-member + decorator pairing ---

def emit_class_members_with_decorators(graph: Graph, class_body: object, src: bytes, class_id: str,
                                        class_fqn: str, index: "RepoIndex", emit_member) -> list[tuple[str, object]]:
    """Walks class_body's children POSITIONALLY (not by field name -- TS's
    class_body does not field-name a decorated member at all, and even an
    UNDECORATED member in a TS-parsed class_body has field_name None,
    verified empirically during planning; only plain JS's class_body uses a
    'member' field consistently). Pairs each run of `decorator` nodes with
    the next non-decorator, non-punctuation child, then calls `emit_member`
    (js/extractor.py's _emit_class_member) for that child. Returns a list
    of (callableId, bodyNode) pairs for every callable member found, so the
    caller can walk each body for CALLS after all members are known (same
    two-pass shape PHP's extractor uses)."""
    pending_decorators: list[object] = []
    results: list[tuple[str, object]] = []

    for child in class_body.named_children:
        if child.type == "decorator":
            pending_decorators.append(child)
            continue
        if child.type not in ("method_definition", "public_field_definition", "field_definition"):
            continue
        result = emit_member(child, src, class_id, class_fqn)
        if result is not None:
            member_id, body_node = result
            results.append((member_id, body_node))
            for dec in pending_decorators:
                _emit_decorator_route(graph, dec, src, member_id)
        pending_decorators = []

    return results


def _decorator_call_parts(decorator_node, src: bytes) -> tuple[str, object | None] | None:
    """A decorator wraps either a bare `identifier` (`@Injectable`) or a
    `call_expression` (`@Get('/x')`) as its second child (after the `@`
    token) -- verified empirically during planning. Returns (name,
    argumentsNodeOrNone)."""
    inner = decorator_node.named_children[0] if decorator_node.named_children else None
    if inner is None:
        return None
    if inner.type == "call_expression":
        fn = inner.child_by_field_name("function")
        if fn is None or fn.type != "identifier":
            return None
        return _text(src, fn), inner.child_by_field_name("arguments")
    if inner.type == "identifier":
        return _text(src, inner), None
    return None


def _emit_decorator_route(graph: Graph, decorator_node, src: bytes, target_id: str) -> None:
    parts = _decorator_call_parts(decorator_node, src)
    if parts is None:
        return
    name, args_node = parts
    verb = annotation_http_verb(name)
    if verb is None:
        return
    path = None
    if args_node is not None:
        for arg in args_node.named_children:
            text = _string_literal_text(arg, src)
            if text is not None and looks_like_route_path(text):
                path = text
                break
    from js.extractor import ensure_annotation_type
    ann_id = ensure_annotation_type(graph, name)
    graph.add_rel(target_id, ann_id, "HAS_ANNOTATION", value=path or "", language="js")


# --- Call-site + routing recognition (walks one file's full AST) ---

def _text(src: bytes, node) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node, src: bytes) -> str | None:
    if node is None or node.type != "string":
        return None
    content = next((c for c in node.named_children if c.type == "string_fragment"), None)
    return _text(src, content) if content is not None else ""


def _positional_args(call_node, src: bytes):
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return []
    return list(args_node.named_children)


def _is_express_route_call(node, src: bytes) -> tuple[str, list, object] | None:
    """Returns (verbName, positionalArgNodes, handlerNode) if `node` looks
    like app.get(path, handler)/router.post(path, handler) -- see module
    docstring for why no receiver name is hardcoded, and why BOTH a
    path-shaped first arg AND a second (handler) arg are required."""
    if node.type != "call_expression":
        return None
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    prop = fn.child_by_field_name("property")
    if prop is None:
        return None
    verb_name = _text(src, prop)
    args = _positional_args(node, src)
    if len(args) < 2:
        return None
    path_text = _string_literal_text(args[0], src)
    if path_text is None or not looks_like_route_path(path_text):
        return None
    route = resolve_call_route(verb_name, [path_text])
    if route is None:
        return None
    return verb_name, args, args[1]


def _resolve_handler_node(handler_node) -> object | None:
    """Returns the Node this handler ultimately refers to for CALLS-target
    lookup purposes -- a function_expression/arrow_function passed inline
    IS itself the target once extractor.py has emitted it as a top-level
    callable only if it was ALSO assigned to a name first (Task 3 only
    walks named top-level callables, not anonymous inline ones) -- so an
    inline anonymous handler can only be resolved if it's a NAMED function
    expression (`function show(req, res) {...}`), matched by simple name
    against the module's callables; a bare identifier reference
    (`router.post('/x', handleCreate)`) is matched the same way. Anonymous
    arrow-function/function-expression handlers passed inline with no name
    at all are a documented, accepted gap -- same "best-effort, no
    placeholder nodes for things that were never extracted" posture as
    every other language's routing recognition."""
    if handler_node.type == "identifier":
        return handler_node
    if handler_node.type == "function_expression":
        name_node = handler_node.child_by_field_name("name")
        return name_node
    return None


def emit_calls_and_routes_in_file(graph: Graph, finfo, index: RepoIndex) -> None:
    src = finfo.src

    def walk(n, ctx: CallContext, caller_id: str | None):
        express_route = _is_express_route_call(n, src)
        if express_route is not None:
            verb_name, args, handler_node = express_route
            handler_name_node = _resolve_handler_node(handler_node)
            if handler_name_node is not None:
                handler_name = _text(src, handler_name_node)
                target_id = index.module_callables.get(finfo.module_qn, {}).get(handler_name)
                if target_id is None:
                    imported = finfo.imports.get(handler_name)
                    if imported:
                        target_id = index.module_callables.get(imported.target_module, {}).get(handler_name)
                if target_id is None and handler_node.type == "function_expression":
                    # An inline named function expression passed directly as
                    # a route handler (`app.get('/x', function show(req, res)
                    # {...})`) is never captured by the top-level scan (Task 3
                    # only walks declared top-level callables) -- emit it as
                    # a Function node here, on first use, same shape Pass 1
                    # would have used had it been a top-level declaration.
                    target_id = callable_id(f"{finfo.module_qn}.{handler_name}")
                    if not graph.has_node(target_id):
                        graph.add_node(target_id, "Function", handler_name, f"{finfo.module_qn}.{handler_name}",
                                       external=False, language="js")
                        graph.add_rel(f"module:{finfo.module_qn}", target_id, "CONTAINS", language="js")
                if target_id is not None:
                    path_text = _string_literal_text(args[0], src)
                    route = resolve_call_route(verb_name, [path_text])
                    if route is not None:
                        http_method, http_path = route
                        from js.extractor import emit_route_annotation
                        emit_route_annotation(graph, target_id, http_method, http_path)
            return  # do not descend into this call's own arguments

        if n.type == "call_expression" and caller_id is not None:
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and _HOOK_CALL_RE.match(_text(src, fn)):
                _maybe_emit_uses_hook(graph, _text(src, fn), caller_id, ctx, index)
            else:
                _maybe_emit_calls_edge(graph, n, src, caller_id, ctx, index)

        if n.type == "new_expression" and caller_id is not None:
            ctor_node = n.child_by_field_name("constructor")
            if ctor_node is not None and ctor_node.type == "identifier":
                result = _resolve_new_expression(_text(src, ctor_node), ctx, index)
                if result is not None:
                    target_id, confidence, reason = result
                    if graph.has_node(target_id):
                        line = n.start_point[0] + 1
                        graph.add_rel(caller_id, target_id, "CALLS", lineNumber=line,
                                      confidence=confidence, reason=reason, language="js")

        for child in n.children:
            walk(child, ctx, caller_id)

    callable_bodies: list[tuple[str, object, str | None]] = []  # (callableId, bodyNode, callerClassFqn)
    for cls in finfo.classes:
        body = cls.node.child_by_field_name("body")
        if body is None:
            continue
        for member in body.named_children:
            if member.type == "decorator":
                continue
            if member.type not in ("method_definition", "public_field_definition", "field_definition"):
                continue
            # A class field's name is in the `property` field, NOT `name`
            # (unlike method_definition, which does use `name`) -- verified
            # empirically, same fix as js/extractor.py's _emit_class_member.
            name_node = (member.child_by_field_name("name") if member.type == "method_definition"
                         else member.child_by_field_name("property"))
            name = _text(src, name_node)
            if member.type == "method_definition":
                is_ctor = name == "constructor"
                m_id = constructor_id(cls.fqn) if is_ctor else callable_id(f"{cls.fqn}.{name}")
                callable_bodies.append((m_id, member.child_by_field_name("body"), cls.fqn))
            else:
                value = member.child_by_field_name("value")
                if value is not None and value.type == "arrow_function":
                    m_id = callable_id(f"{cls.fqn}.{name}")
                    callable_bodies.append((m_id, value.child_by_field_name("body"), cls.fqn))

    for m_id, body_node, caller_class in callable_bodies:
        if body_node is None:
            continue
        ctx = CallContext(finfo.module_qn, caller_class, finfo.file_path, finfo.imports)
        for stmt in body_node.children:
            walk(stmt, ctx, m_id)

    for fn in finfo.callables:
        f_id = callable_id(fn.fqn)
        ctx = CallContext(finfo.module_qn, None, finfo.file_path, finfo.imports)
        body_node = fn.node.child_by_field_name("body")
        if body_node is not None:
            if body_node.type == "statement_block":
                for stmt in body_node.children:
                    walk(stmt, ctx, f_id)
            else:
                # Concise arrow-function body (`(a, b) => a + b`) -- the
                # "body" IS the expression itself, not a statement_block.
                walk(body_node, ctx, f_id)

    # Top-level (script) statements -- e.g. a routes.js file's
    # app.get(...) calls with no enclosing function at all.
    top_ctx = CallContext(finfo.module_qn, None, finfo.file_path, finfo.imports)
    handled_node_ids = {id(cls.node) for cls in finfo.classes} | {id(fn.node) for fn in finfo.callables}
    for child in finfo.top_level_nodes:
        unwrapped = child
        if child.type == "export_statement":
            decl = child.child_by_field_name("declaration")
            if decl is not None:
                unwrapped = decl
        if id(unwrapped) in handled_node_ids:
            continue
        walk(child, top_ctx, None)


def _maybe_emit_uses_hook(graph: Graph, hook_name: str, caller_id: str, ctx: CallContext, index: RepoIndex) -> None:
    target_id = index.module_callables.get(ctx.module_qn, {}).get(hook_name)
    if target_id is None:
        imported = ctx.imports.get(hook_name)
        if imported:
            target_id = index.module_callables.get(imported.target_module, {}).get(hook_name)
    if target_id is None:
        # External hook (e.g. React's own useState) -- a synthetic
        # placeholder Hook node, mirroring every other language's
        # external-reference-placeholder pattern.
        target_id = f"hook:external:{hook_name}"
        if not graph.has_node(target_id):
            graph.add_node(target_id, "Hook", hook_name, hook_name, external=True, language="js")
    if graph.has_node(caller_id) and graph.has_node(target_id):
        graph.add_rel(caller_id, target_id, "USES_HOOK", language="js")


def _maybe_emit_calls_edge(graph: Graph, call_node, src: bytes, caller_id: str,
                            ctx: CallContext, index: RepoIndex) -> None:
    from js.extractor import url_literal_args, emit_http_dependency

    fn = call_node.child_by_field_name("function")
    result: tuple[str, float, str] | None = None

    if fn is not None and fn.type == "identifier":
        result = _resolve_bare_call(_text(src, fn), ctx, index)
    elif fn is not None and fn.type == "member_expression":
        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        if obj is None or prop is None:
            pass
        elif obj.type == "this":
            result = _resolve_this_method(_text(src, prop), ctx, index)
        elif obj.type == "identifier":
            obj_name = _text(src, obj)
            is_known_class = (obj_name in index.module_classes.get(ctx.module_qn, {})
                               or obj_name in ctx.imports)
            if is_known_class:
                result = _resolve_class_static_call(obj_name, _text(src, prop), ctx, index)
            else:
                result = _global_fallback(_text(src, prop), index)

    for url in url_literal_args(call_node, src):
        emit_http_dependency(graph, caller_id, f"http-call:{caller_id}", url)

    if result is not None:
        target_id, confidence, reason = result
        if not graph.has_node(target_id):
            return
        line = call_node.start_point[0] + 1
        graph.add_rel(caller_id, target_id, "CALLS", lineNumber=line,
                      confidence=confidence, reason=reason, language="js")
