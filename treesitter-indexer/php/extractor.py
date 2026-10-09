"""
Structural tree-sitter PHP extractor: AST -> node/rel graph targeting
lumen's existing canonical vocabulary (graph_store.py), mirroring
python/extractor.py's shape.

Namespaces map to Package nodes (same structural role as Java's packages;
PHP's native backslash separator is preserved as-is in qualifiedName/id,
same "preserve parser-native fidelity" philosophy CLAUDE.md documents for
every language). Files with no `namespace` declaration share one root
Package with an empty-string qualifiedName -- mirrors Java's handling of
the unnamed/default package.

Traits are modeled as Class nodes with `type: "trait"` (the existing,
already-declared, already-allowlisted generic `type` column -- NOT a new
`phpKind` property, which would repeat the exact "property not in the
write-allowlist, silently dropped" bug class already found in the *old*
PHP engine).

Constructor property promotion (`function __construct(private Foo $x)`)
emits BOTH a Parameter/HAS_PARAMETER edge (like any other parameter) AND a
Field/CONTAINS edge from the owning Class -- real PHP semantics: a promoted
parameter literally becomes an object property.

Known, accepted divergences (documented, not "fixed" mid-port, same spirit
as java/extractor.py's and python/extractor.py's own divergence lists):
anonymous functions/closures are not modeled as their own nodes at all
(their bodies ARE still walked for CALLS and routing recognition, attributed
to the enclosing Method/Function/Constructor, or to a synthetic per-file
"script" container -- see emit_calls_and_routes_in_file in php/calls.py);
only `use App\\X;` class-style imports are resolved (no `use function`/
`use const` imports); a bare class reference is only resolved against the
current namespace or an explicit `use` import, never a wildcard/grouped
`use App\\{A, B};` import.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dc_field

from tree_sitter import Language, Node, Parser
import tree_sitter_php as tsphp

from graph_model import Graph
from http_url_detection import looks_like_url
from php.calls import ImportedName, RepoIndex, constructor_id, function_id, method_id

PHP_LANGUAGE = Language(tsphp.language_php())

_EXCLUDED_DIRS = {
    ".git", "vendor", "node_modules", "storage", "bootstrap",
    "tests", "test", ".idea",
}

PHP_PRIMITIVE_TYPES = {
    "int", "float", "string", "bool", "array", "mixed", "void", "null",
    "object", "callable", "iterable", "self", "static", "never", "false", "true",
}


def _text(src: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node: Node | None, src: bytes) -> str | None:
    """Single-quoted PHP strings are a `string` node; double-quoted strings
    -- even with NO interpolation -- are an `encapsed_string` node. Both wrap
    their content in a `string_content` child."""
    if node is None or node.type not in ("string", "encapsed_string"):
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return _text(src, content) if content is not None else ""


def _named_child_of_type(node: Node, child_type: str) -> Node | None:
    return next((c for c in node.named_children if c.type == child_type), None)


def _type_text(type_node: Node | None, src: bytes) -> str:
    """Handles primitive_type, named_type, optional_type (?Foo), and
    qualified_name -- strips a leading `?` for nullable types, since this
    engine doesn't model nullability, only the underlying type."""
    if type_node is None:
        return ""
    if type_node.type == "optional_type":
        inner = type_node.named_children[0] if type_node.named_children else None
        return _type_text(inner, src)
    return _text(src, type_node)


@dataclass
class ClassInfo:
    node: Node
    simple_name: str
    fqn: str
    kind: str  # "class" | "interface" | "trait"


@dataclass
class FunctionInfo:
    node: Node
    simple_name: str
    fqn: str


@dataclass
class FileInfo:
    file_path: str
    src: bytes
    namespace: str
    imports: dict[str, ImportedName]
    top_level_nodes: list = dc_field(default_factory=list)
    classes: list[ClassInfo] = dc_field(default_factory=list)
    functions: list[FunctionInfo] = dc_field(default_factory=list)


def _namespace_of_file(tree_root: Node, src: bytes) -> str:
    ns_node = next((c for c in tree_root.children if c.type == "namespace_definition"), None)
    if ns_node is None:
        return ""
    name_node = _named_child_of_type(ns_node, "namespace_name")
    return _text(src, name_node) if name_node is not None else ""


def _imports(tree_root: Node, src: bytes) -> dict[str, ImportedName]:
    result: dict[str, ImportedName] = {}
    for child in tree_root.children:
        if child.type != "namespace_use_declaration":
            continue
        for clause in child.named_children:
            if clause.type != "namespace_use_clause":
                continue
            name_node = _named_child_of_type(clause, "qualified_name") or _named_child_of_type(clause, "name")
            if name_node is None:
                continue
            fqn = _text(src, name_node)
            alias_node = next((c for c in clause.named_children if c.type == "name" and c is not name_node), None)
            local = _text(src, alias_node) if alias_node is not None else fqn.rsplit("\\", 1)[-1]
            result[local] = ImportedName("class", fqn)
    return result


def _top_level_scan(file_path: str, src: bytes, tree_root: Node) -> FileInfo:
    namespace = _namespace_of_file(tree_root, src)
    imports = _imports(tree_root, src)
    info = FileInfo(file_path, src, namespace, imports)
    info.top_level_nodes = list(tree_root.children)

    def qualify(simple: str) -> str:
        return f"{namespace}\\{simple}" if namespace else simple

    for child in tree_root.children:
        if child.type in ("class_declaration", "interface_declaration", "trait_declaration"):
            kind = {"class_declaration": "class", "interface_declaration": "interface",
                    "trait_declaration": "trait"}[child.type]
            name_node = child.child_by_field_name("name")
            simple = _text(src, name_node)
            info.classes.append(ClassInfo(child, simple, qualify(simple), kind))
        elif child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            simple = _text(src, name_node)
            info.functions.append(FunctionInfo(child, simple, qualify(simple)))

    return info


def _build_repo_index(file_infos: list[FileInfo]) -> RepoIndex:
    index = RepoIndex()
    for finfo in file_infos:
        index.namespace_file.setdefault(finfo.namespace, finfo.file_path)
        index.namespace_functions.setdefault(finfo.namespace, set())
        for cls in finfo.classes:
            index.internal_classes.add(cls.fqn)
            index.class_file[cls.fqn] = finfo.file_path
            index.class_methods.setdefault(cls.fqn, {})
        for fn in finfo.functions:
            index.namespace_functions[finfo.namespace].add(fn.simple_name)
    return index


def _resolve_class_name(simple_or_qualified: str, namespace: str,
                         imports: dict[str, ImportedName], index: RepoIndex) -> str | None:
    """Resolution order: leading-backslash fully-qualified -> same-namespace
    -> `use`-imported alias -> unresolved (falls through to the global
    fallback tier for CALLS; handler-array resolution in the routing walk
    simply fails to attach a target if this returns None).

    Deliberately a separate function from php/calls.py's near-identical
    _resolve_simple_class, rather than a shared import: that one also
    resolves "self"/"static" against a call-site's MethodCallContext, which
    has no equivalent here (EXTENDS/IMPLEMENTS/OF_TYPE resolution has no
    notion of a "caller"). Accepted, documented duplication rather than a
    forced shared abstraction across two different resolution shapes."""
    if simple_or_qualified.startswith("\\"):
        candidate = simple_or_qualified[1:]
        return candidate if candidate in index.internal_classes else None
    same_ns = f"{namespace}\\{simple_or_qualified}" if namespace else simple_or_qualified
    if same_ns in index.internal_classes:
        return same_ns
    imported = imports.get(simple_or_qualified)
    if imported and imported.kind == "class" and imported.target in index.internal_classes:
        return imported.target
    return None


_HTTP_DEP_COUNTER = {"n": 0}


def _emit_http_dependency(graph: Graph, owner_id: str, key: str, value: str) -> None:
    _HTTP_DEP_COUNTER["n"] += 1
    qualified_name = f"code:{owner_id}#{key}#{_HTTP_DEP_COUNTER['n']}"
    prop_id = f"configprop:{qualified_name}"
    graph.add_node(prop_id, "ConfigProperty", key, qualified_name, code=value, external=False, language="php")
    graph.add_rel(owner_id, prop_id, "REFERENCES_CONFIG", language="php")


def _url_literal_args(call_node: Node, src: bytes) -> list[str]:
    args = _named_child_of_type(call_node, "arguments")
    if args is None:
        return []
    out = []
    for arg in args.named_children:
        if arg.type != "argument":
            continue
        value_node = arg.named_children[0] if arg.named_children else None
        text = _string_literal_text(value_node, src) if value_node is not None else None
        if text is not None and looks_like_url(text):
            out.append(text)
    return out


_ANNOTATION_TYPE_CACHE: dict[str, str] = {}


def _ensure_annotation_type(graph: Graph, name: str) -> str:
    ann_id = f"annotationtype:{name}"
    if not graph.has_node(ann_id):
        graph.add_node(ann_id, "AnnotationType", name, name, external=True, language="php")
    return ann_id


def _emit_route_annotation(graph: Graph, method_id_: str, http_method: str, http_path: str | None,
                            middleware: str | None) -> None:
    verb_name = http_method.capitalize() if http_method != "HTTP" else "Mapping"
    ann_id = _ensure_annotation_type(graph, verb_name)
    graph.add_rel(method_id_, ann_id, "HAS_ANNOTATION", value=http_path or "", language="php")
    if middleware:
        mw_id = _ensure_annotation_type(graph, "Middleware")
        graph.add_rel(method_id_, mw_id, "HAS_ANNOTATION", value=middleware, language="php")


def _ensure_type_node(graph: Graph, type_text: str, namespace: str,
                       imports: dict[str, ImportedName], index: RepoIndex) -> str | None:
    simple = type_text.strip().lstrip("\\")
    if not simple or simple in PHP_PRIMITIVE_TYPES:
        return None
    resolved = _resolve_class_name(simple, namespace, imports, index)
    if resolved:
        return f"class:{resolved}"
    ext_id = f"class:external:{simple}"
    if not graph.has_node(ext_id):
        graph.add_node(ext_id, "Class", simple, simple, external=True, language="php")
    return ext_id


def _emit_parameters(graph: Graph, owner_id: str, formal_params: Node | None, src: bytes,
                      namespace: str, imports: dict[str, ImportedName], index: RepoIndex,
                      class_id: str | None, class_fqn: str | None) -> None:
    """class_id/class_fqn are non-None only for a Constructor -- property
    promotion emits an additional Field/CONTAINS edge in that case."""
    if formal_params is None:
        return
    for p in formal_params.named_children:
        if p.type not in ("simple_parameter", "property_promotion_parameter", "variadic_parameter"):
            continue
        name_node = p.child_by_field_name("name")
        name = _text(src, name_node).lstrip("$")
        type_text = _type_text(p.child_by_field_name("type"), src)

        param_id = f"param:{owner_id}.{name}"
        graph.add_node(param_id, "Parameter", name, name, external=False, language="php")
        graph.add_rel(owner_id, param_id, "HAS_PARAMETER", language="php")
        target_id = _ensure_type_node(graph, type_text, namespace, imports, index) if type_text else None
        if target_id:
            graph.add_rel(param_id, target_id, "OF_TYPE", language="php")

        if p.type == "property_promotion_parameter" and class_id is not None and class_fqn is not None:
            field_id = f"field:{class_fqn}.{name}"
            graph.add_node(field_id, "Field", name, f"{class_fqn}.{name}", external=False, language="php")
            graph.add_rel(class_id, field_id, "CONTAINS", language="php")
            if target_id:
                graph.add_rel(field_id, target_id, "OF_TYPE", language="php")


def _emit_class_constants(graph: Graph, const_decl: Node, src: bytes, class_id: str, class_fqn: str) -> None:
    """Modeled as Field nodes with `type: "constant"` -- the same generic,
    already-allowlisted `type` column used for trait-marking (see module
    docstring), NOT a new `phpKind` property."""
    for elem in const_decl.named_children:
        if elem.type != "const_element":
            continue
        name_node = elem.named_children[0] if elem.named_children else None
        if name_node is None or name_node.type != "name":
            continue
        name = _text(src, name_node)
        field_id = f"field:{class_fqn}.{name}"
        graph.add_node(field_id, "Field", name, f"{class_fqn}.{name}",
                       external=False, language="php", type="constant")
        graph.add_rel(class_id, field_id, "CONTAINS", language="php")


def _emit_property(graph: Graph, prop_decl: Node, src: bytes, class_id: str, class_fqn: str,
                    namespace: str, imports: dict[str, ImportedName], index: RepoIndex) -> None:
    type_text = _type_text(prop_decl.child_by_field_name("type"), src)
    for elem in prop_decl.named_children:
        if elem.type != "property_element":
            continue
        name_node = elem.child_by_field_name("name")
        name = _text(src, name_node).lstrip("$")
        field_id = f"field:{class_fqn}.{name}"
        graph.add_node(field_id, "Field", name, f"{class_fqn}.{name}", external=False, language="php")
        graph.add_rel(class_id, field_id, "CONTAINS", language="php")
        if type_text:
            target_id = _ensure_type_node(graph, type_text, namespace, imports, index)
            if target_id:
                graph.add_rel(field_id, target_id, "OF_TYPE", language="php")


def _emit_method_or_constructor(graph: Graph, method_decl: Node, src: bytes, class_id: str, class_fqn: str,
                                 namespace: str, imports: dict[str, ImportedName], index: RepoIndex) -> str:
    name = _text(src, method_decl.child_by_field_name("name"))
    is_ctor = name == "__construct"
    m_id = constructor_id(class_fqn) if is_ctor else method_id(class_fqn, name)
    table = "Constructor" if is_ctor else "Method"
    graph.add_node(m_id, table, name, f"{class_fqn}.{name}", external=False, language="php")
    graph.add_rel(class_id, m_id, "CONTAINS", language="php")
    if not is_ctor:
        index.class_methods.setdefault(class_fqn, {})[name] = m_id

    _emit_parameters(graph, m_id, method_decl.child_by_field_name("parameters"), src,
                      namespace, imports, index, class_id if is_ctor else None, class_fqn if is_ctor else None)

    return_type_node = method_decl.child_by_field_name("return_type")
    if return_type_node is not None:
        type_text = _type_text(return_type_node, src)
        target_id = _ensure_type_node(graph, type_text, namespace, imports, index) if type_text else None
        if target_id:
            graph.add_rel(m_id, target_id, "RETURNS", language="php")
    return m_id


def extract_repo(repo_path: str) -> Graph:
    parser = Parser(PHP_LANGUAGE)
    graph = Graph()

    php_files: list[str] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        for fname in files:
            if fname.endswith(".php"):
                php_files.append(os.path.join(root, fname))

    file_infos: list[FileInfo] = []
    for fpath in php_files:
        with open(fpath, "rb") as fh:
            src = fh.read()
        tree = parser.parse(src)
        file_infos.append(_top_level_scan(fpath, src, tree.root_node))

    index = _build_repo_index(file_infos)

    # --- Pass 1: Package + Class/Interface/Function nodes ---
    package_id_by_ns: dict[str, str] = {}
    class_id_by_fqn: dict[str, str] = {}

    def package_id(namespace: str) -> str:
        if namespace not in package_id_by_ns:
            pkg_id = f"package:{namespace}"
            package_id_by_ns[namespace] = pkg_id
            graph.add_node(pkg_id, "Package", namespace.rsplit("\\", 1)[-1] if namespace else "",
                           namespace, external=False, language="php")
        return package_id_by_ns[namespace]

    for finfo in file_infos:
        pkg_id = package_id(finfo.namespace)
        for cls in finfo.classes:
            class_id = f"class:{cls.fqn}"
            class_id_by_fqn[cls.fqn] = class_id
            table = "Interface" if cls.kind == "interface" else "Class"
            extra = {"type": "trait"} if cls.kind == "trait" else {}
            graph.add_node(class_id, table, cls.simple_name, cls.fqn, external=False, language="php", **extra)
            graph.add_rel(pkg_id, class_id, "CONTAINS", language="php")
        for fn in finfo.functions:
            f_id = function_id(fn.fqn)
            graph.add_node(f_id, "Function", fn.simple_name, fn.fqn, external=False, language="php")
            graph.add_rel(pkg_id, f_id, "CONTAINS", language="php")
            index.function_ids[fn.fqn] = f_id

    # --- Pass 2: EXTENDS/IMPLEMENTS, members (properties/methods/params/returns) ---
    for finfo in file_infos:
        src = finfo.src
        for cls in finfo.classes:
            class_id = class_id_by_fqn[cls.fqn]
            base_clause = _named_child_of_type(cls.node, "base_clause")
            if base_clause is not None:
                for name_node in base_clause.named_children:
                    if name_node.type != "name":
                        continue
                    base_fqn = _resolve_class_name(_text(src, name_node), finfo.namespace, finfo.imports, index)
                    target_id = (f"class:{base_fqn}" if base_fqn else _ensure_type_node(
                        graph, _text(src, name_node), finfo.namespace, finfo.imports, index))
                    if target_id:
                        graph.add_rel(class_id, target_id, "EXTENDS", language="php")

            iface_clause = _named_child_of_type(cls.node, "class_interface_clause")
            if iface_clause is not None:
                for name_node in iface_clause.named_children:
                    if name_node.type != "name":
                        continue
                    iface_fqn = _resolve_class_name(_text(src, name_node), finfo.namespace, finfo.imports, index)
                    target_id = (f"class:{iface_fqn}" if iface_fqn else _ensure_type_node(
                        graph, _text(src, name_node), finfo.namespace, finfo.imports, index))
                    if target_id:
                        graph.add_rel(class_id, target_id, "IMPLEMENTS", language="php")

            body = cls.node.child_by_field_name("body")
            if body is None:
                continue
            for member in body.named_children:
                if member.type == "method_declaration":
                    _emit_method_or_constructor(graph, member, src, class_id, cls.fqn,
                                                 finfo.namespace, finfo.imports, index)
                elif member.type == "property_declaration":
                    _emit_property(graph, member, src, class_id, cls.fqn, finfo.namespace, finfo.imports, index)
                elif member.type == "const_declaration":
                    _emit_class_constants(graph, member, src, class_id, cls.fqn)

        for fn in finfo.functions:
            f_id = index.function_ids[fn.fqn]
            _emit_parameters(graph, f_id, fn.node.child_by_field_name("parameters"), src,
                              finfo.namespace, finfo.imports, index, None, None)
            return_type_node = fn.node.child_by_field_name("return_type")
            if return_type_node is not None:
                type_text = _type_text(return_type_node, src)
                target_id = _ensure_type_node(graph, type_text, finfo.namespace, finfo.imports, index) if type_text else None
                if target_id:
                    graph.add_rel(f_id, target_id, "RETURNS", language="php")

    # --- Pass 3: global simple-name index (needs every Method/Function id known), then CALLS + routing ---
    for class_fqn, methods in index.class_methods.items():
        for mname, mid in methods.items():
            index.simple_callable_to_fqns.setdefault(mname, []).append(mid)
    for fqn, fid in index.function_ids.items():
        simple = fqn.rsplit("\\", 1)[-1]
        index.simple_callable_to_fqns.setdefault(simple, []).append(fid)

    from php.calls import emit_calls_and_routes_in_file  # local import: avoids a module-load cycle with resolve_call_route

    for finfo in file_infos:
        emit_calls_and_routes_in_file(graph, finfo, index)

    return graph
