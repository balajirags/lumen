"""
Wave A structural extractor: tree-sitter Java AST -> node/rel graph, targeting
lumen's existing canonical vocabulary (see graph_store.py) so downstream
toolkit.py/agent-stage code needs no changes.

Mirrors indexer/app/.../JavaSourceParser.java's structural extraction (verified
by direct read of that file): packages, classes/interfaces/enums/records,
methods/constructors/fields/parameters, EXTENDS/IMPLEMENTS, a single generic
HAS_ANNOTATION/AnnotationType mechanism (Spring routes and JPA entities are
both just "an annotation" -- no special-cased extraction needed), best-effort
OVERRIDES, and CALLS with confidence tiers (delegated to calls.py).

Known, accepted divergences from JavaSourceParser (documented, not "fixed"
mid-port -- see the plan): nested types are flattened to package-level
containment (CONTAINS schema has no Class->Class combo); CALLS/OVERRIDES match
by (declared type, method name, argument count) rather than full JavaParser
symbol-resolved overloads; Enum/Record CONTAINS/HAS_ANNOTATION/IMPLEMENTS edges
silently no-op exactly as they already do in the existing shared schema (the
REL_DEFINITIONS FROM/TO combos never included Enum/Record for those tables --
verified against both store.py and KuzuGraphStore.java, so this is a
pre-existing gap being replicated, not introduced).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dc_field

from tree_sitter import Language, Node, Parser
import tree_sitter_java as tsjava

from java.calls import MethodCallContext, RepoIndex, resolve_call_target

JAVA_LANGUAGE = Language(tsjava.language())

TYPE_DECL_KINDS = {
    "class_declaration": "Class",
    "interface_declaration": "Interface",
    "enum_declaration": "Enum",
    "record_declaration": "Record",
}

PRIMITIVE_TYPES = {
    "void", "int", "long", "short", "byte", "char", "boolean", "float", "double",
}

HTTP_ANNOTATIONS = {"GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping"}


def _text(src: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _strip_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def _modifiers_node(decl_node: Node) -> Node | None:
    for child in decl_node.children:
        if child.type == "modifiers":
            return child
    return None


def _annotation_nodes(decl_node: Node) -> list[Node]:
    mods = _modifiers_node(decl_node)
    if mods is None:
        return []
    return [c for c in mods.children if c.type in ("marker_annotation", "annotation")]


def _has_modifier_keyword(decl_node: Node, src: bytes, keyword: str) -> bool:
    mods = _modifiers_node(decl_node)
    if mods is None:
        return False
    return any(_text(src, c) == keyword for c in mods.children)


def _visibility(decl_node: Node, src: bytes) -> str:
    for kw in ("public", "private", "protected"):
        if _has_modifier_keyword(decl_node, src, kw):
            return kw
    return "package"


def _annotation_name_and_value(ann_node: Node, src: bytes) -> tuple[str, str | None]:
    name = _text(src, ann_node.child_by_field_name("name"))
    if ann_node.type == "marker_annotation":
        return name, None
    args = ann_node.child_by_field_name("arguments")
    if args is None:
        return name, None
    named = args.named_children
    if len(named) == 1 and named[0].type != "element_value_pair":
        return name, _strip_quotes(_text(src, named[0]))
    value_pair = None
    for pair in named:
        if pair.type != "element_value_pair":
            continue
        key = _text(src, pair.child_by_field_name("key"))
        if key == "value":
            value_pair = pair
            break
    if value_pair is None and named:
        value_pair = named[0] if named[0].type == "element_value_pair" else None
    if value_pair is not None:
        return name, _strip_quotes(_text(src, value_pair.child_by_field_name("value")))
    return name, None


def _find_type_declarations(node: Node, out: list[Node]) -> None:
    if node.type in TYPE_DECL_KINDS:
        out.append(node)
    for child in node.children:
        _find_type_declarations(child, out)


def _package_name(tree_root: Node, src: bytes) -> str:
    for child in tree_root.children:
        if child.type == "package_declaration":
            for c in child.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return _text(src, c)
    return ""


def _imports(tree_root: Node, src: bytes) -> tuple[dict[str, str], list[str]]:
    explicit: dict[str, str] = {}
    wildcards: list[str] = []
    for child in tree_root.children:
        if child.type != "import_declaration":
            continue
        is_wildcard = any(c.type == "asterisk" for c in child.children)
        scoped = next((c for c in child.children if c.type in ("scoped_identifier", "identifier")), None)
        if scoped is None:
            continue
        full = _text(src, scoped)
        if is_wildcard:
            wildcards.append(full)
        else:
            simple = full.rsplit(".", 1)[-1]
            explicit[simple] = full
    return explicit, wildcards


@dataclass
class TypeDeclInfo:
    node: Node
    kind: str
    simple_name: str
    fqn: str
    file_path: str
    package: str


@dataclass
class FileInfo:
    file_path: str
    src: bytes
    tree_root: Node
    package: str
    imports: dict[str, str]
    wildcards: list[str]
    type_decls: list[TypeDeclInfo] = dc_field(default_factory=list)


def _pass1_scan_file(file_path: str, src: bytes, tree_root: Node) -> FileInfo:
    package = _package_name(tree_root, src)
    imports, wildcards = _imports(tree_root, src)
    info = FileInfo(file_path, src, tree_root, package, imports, wildcards)

    decl_nodes: list[Node] = []
    _find_type_declarations(tree_root, decl_nodes)
    for decl in decl_nodes:
        kind = TYPE_DECL_KINDS[decl.type]
        name_node = decl.child_by_field_name("name")
        simple_name = _text(src, name_node)
        # Nested types are flattened to package-level containment (see module
        # docstring) -- fqn intentionally drops the outer type's name.
        fqn = f"{package}.{simple_name}" if package else simple_name
        info.type_decls.append(TypeDeclInfo(decl, kind, simple_name, fqn, file_path, package))
    return info


def _build_repo_index(file_infos: list[FileInfo]) -> RepoIndex:
    index = RepoIndex()
    for finfo in file_infos:
        index.file_imports[finfo.file_path] = finfo.imports
        index.file_wildcard_packages[finfo.file_path] = finfo.wildcards
        for decl in finfo.type_decls:
            index.internal_types.add(decl.fqn)
            index.type_file[decl.fqn] = decl.file_path
            index.type_package[decl.fqn] = decl.package
            index.simple_name_to_fqns.setdefault(decl.simple_name, []).append(decl.fqn)
    return index


def _ensure_type_node(graph, name_text: str, index: RepoIndex, from_file: str, from_package: str, default_kind: str):
    resolved = index.resolve_type_name(name_text, from_file, from_package)
    if resolved:
        return f"type:{resolved}", resolved, True
    simple = name_text.split("<", 1)[0].rstrip("[]").strip()
    qualified_external = index.qualify_external(simple, from_file)
    ext_id = f"type:external:{qualified_external}"
    if not graph.has_node(ext_id):
        graph.add_node(ext_id, default_kind, simple, qualified_external, external=True)
    return ext_id, None, False


def _collect_supertypes(decl: "TypeDeclInfo", finfo: "FileInfo", index: RepoIndex) -> list[tuple[str, str | None, bool]]:
    """Returns [(rawSupertypeText, resolvedInternalFqnOrNone, isSuperclassClause), ...]
    for a type's superclass/super_interfaces/extends_interfaces clauses --
    shared by _emit_supertypes and _emit_calls_and_overrides so both see the
    same list, including entries that resolve to nothing internal (needed so
    OVERRIDES can still target an external/framework method -- see
    _emit_calls_and_overrides)."""
    src = finfo.src
    items: list[tuple[str, str | None, bool]] = []
    for child in decl.node.children:
        if child.type == "superclass":
            type_ref = next((c for c in child.named_children), None)
            if type_ref is not None:
                raw = _text(src, type_ref)
                items.append((raw, index.resolve_type_name(raw, finfo.file_path, finfo.package), True))
        elif child.type in ("super_interfaces", "extends_interfaces"):
            type_list = next((c for c in child.children if c.type == "type_list"), None)
            if type_list is None:
                continue
            for type_ref in type_list.named_children:
                raw = _text(src, type_ref)
                items.append((raw, index.resolve_type_name(raw, finfo.file_path, finfo.package), False))
    return items


def _method_id(type_fqn: str, method_name: str, arity: int) -> str:
    return f"method:{type_fqn}#{method_name}/{arity}"


def _ctor_id(type_fqn: str, arity: int) -> str:
    return f"ctor:{type_fqn}#/{arity}"


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.relationships: list[dict] = []

    def add_node(self, node_id, node_type, name, qualified_name, **props):
        if node_id in self.nodes:
            return
        self.nodes[node_id] = {
            "id": node_id, "type": node_type, "name": name,
            "qualifiedName": qualified_name, "properties": props,
        }

    def has_node(self, node_id) -> bool:
        return node_id in self.nodes

    def add_rel(self, source_id, target_id, rel_type, **props):
        self.relationships.append({
            "sourceId": source_id, "targetId": target_id, "type": rel_type, "properties": props,
        })

    def to_json(self) -> dict:
        return {"nodes": list(self.nodes.values()), "relationships": self.relationships}


def _throws_types(method_node: Node, src: bytes) -> list[str]:
    result = []
    for child in method_node.children:
        if child.type == "throws":
            for c in child.named_children:
                result.append(_text(src, c))
    return result


def _formal_params(params_node: Node | None, src: bytes) -> list[tuple[str, str]]:
    """Returns [(paramName, declaredTypeText), ...]."""
    if params_node is None:
        return []
    out = []
    for p in params_node.named_children:
        if p.type not in ("formal_parameter", "spread_parameter"):
            continue
        type_node = p.child_by_field_name("type")
        name_node = p.child_by_field_name("name")
        out.append((_text(src, name_node), _text(src, type_node)))
    return out


def _local_var_types(body_node: Node | None, src: bytes) -> dict[str, str]:
    """Best-effort scan for local_variable_declaration types (Wave A scope only)."""
    result: dict[str, str] = {}
    if body_node is None:
        return result

    def walk(n: Node):
        if n.type == "local_variable_declaration":
            type_node = n.child_by_field_name("type")
            type_text = _text(src, type_node)
            for decl in n.children_by_field_name("declarator"):
                name_node = decl.child_by_field_name("name")
                if name_node is not None:
                    result[_text(src, name_node)] = type_text
        for child in n.children:
            walk(child)

    walk(body_node)
    return result


def _receiver_text(call_node: Node, src: bytes) -> str | None:
    """Extracts the scope/receiver expression text of a method_invocation, or
    None for implicit-`this` calls (no explicit receiver)."""
    obj = call_node.child_by_field_name("object")
    if obj is None:
        return None
    return _text(src, obj)


def _count_args(call_node: Node) -> int:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return 0
    return len([c for c in args.named_children])


def _emit_calls_in_body(graph: Graph, body_node: Node | None, src: bytes, caller_id: str,
                         ctx: MethodCallContext, index: RepoIndex) -> None:
    if body_node is None:
        return

    def walk(n: Node):
        if n.type == "method_invocation":
            name_node = n.child_by_field_name("name")
            method_name = _text(src, name_node)
            receiver = _receiver_text(n, src)
            arg_count = _count_args(n)
            line = n.start_point[0] + 1
            result = resolve_call_target(receiver, method_name, arg_count, ctx, index)
            if result is not None:
                target_type_fqn, confidence, reason = result
                target_id = _method_id(target_type_fqn, method_name, arg_count)
                if not graph.has_node(target_id):
                    # Best-effort call resolved to a type but not a specific
                    # known declaration -- create an inferred placeholder,
                    # mirroring JavaSourceParser.bestEffortCall's behavior.
                    graph.add_node(target_id, "METHOD", method_name,
                                    f"{target_type_fqn}.{method_name}", external=False, inferred=True)
                    graph.add_rel(f"type:{target_type_fqn}", target_id, "CONTAINS")
                graph.add_rel(caller_id, target_id, "CALLS", lineNumber=line,
                              confidence=confidence, reason=reason)
        for child in n.children:
            walk(child)

    walk(body_node)


def _detect_source_root(project_root: str) -> str:
    """Ported from App.java's detectSourceRoot (Java-only case): standard Maven
    /Gradle layout puts sources under src/main/java, TEST sources under
    src/test/java -- the latter is deliberately never scanned. Falls back to
    src/main or src for non-standard layouts, then the project root itself."""
    for candidate in ("src/main/java", "src/main", "src"):
        path = os.path.join(project_root, candidate)
        if os.path.isdir(path):
            return path
    return project_root


def _detect_modules(project_root: str) -> list[str]:
    """Ported from App.java's detectModules: a subdirectory counts as its own
    module if it has both a build file and a src layout of its own."""
    modules: list[str] = []
    try:
        for name in sorted(os.listdir(project_root)):
            child = os.path.join(project_root, name)
            if not os.path.isdir(child):
                continue
            has_own_build = any(
                os.path.exists(os.path.join(child, f)) for f in ("build.gradle", "build.gradle.kts", "pom.xml")
            )
            has_src = any(
                os.path.isdir(os.path.join(child, d)) for d in ("src/main/java", "src/main", "src")
            )
            if has_own_build and has_src:
                modules.append(child)
    except OSError:
        pass

    if not modules:
        return [project_root]

    if any(os.path.isdir(os.path.join(project_root, d)) for d in ("src/main/java", "src/main")):
        modules.insert(0, project_root)
    return modules


def extract_repo(repo_path: str) -> Graph:
    parser = Parser(JAVA_LANGUAGE)
    graph = Graph()

    java_files = []
    for module_root in _detect_modules(repo_path):
        source_root = _detect_source_root(module_root)
        for root, dirs, files in os.walk(source_root):
            dirs[:] = [d for d in dirs if d not in (".git", "target", "build", "node_modules", ".venv")]
            for fname in files:
                if fname.endswith(".java"):
                    java_files.append(os.path.join(root, fname))

    file_infos: list[FileInfo] = []
    for fpath in java_files:
        with open(fpath, "rb") as fh:
            src = fh.read()
        tree = parser.parse(src)
        file_infos.append(_pass1_scan_file(fpath, src, tree.root_node))

    index = _build_repo_index(file_infos)

    # type_fqn -> [(methodName, arity, methodNodeId)], used for OVERRIDES lookups.
    type_methods: dict[str, list[tuple[str, int, str]]] = {}
    # type_fqn -> declaring node id, so EXTENDS/IMPLEMENTS/CONTAINS have targets.
    type_node_id: dict[str, str] = {}

    # --- Pass 2a: emit Package + Type nodes first, so every internal type has
    # a node before any EXTENDS/IMPLEMENTS/CONTAINS/CALLS edge references it.
    packages_seen: set[str] = set()
    for finfo in file_infos:
        if finfo.package and finfo.package not in packages_seen:
            packages_seen.add(finfo.package)
            graph.add_node(f"package:{finfo.package}", "PACKAGE", finfo.package, finfo.package)
        for decl in finfo.type_decls:
            type_id = f"type:{decl.fqn}"
            type_node_id[decl.fqn] = type_id
            graph.add_node(
                type_id, decl.kind.upper(), decl.simple_name, decl.fqn,
                visibility=_visibility(decl.node, finfo.src),
                isAbstract=_has_modifier_keyword(decl.node, finfo.src, "abstract"),
                isFinal=_has_modifier_keyword(decl.node, finfo.src, "final"),
                external=False,
            )
            if finfo.package:
                graph.add_rel(f"package:{finfo.package}", type_id, "CONTAINS")
            type_methods[decl.fqn] = []

    # --- Pass 2b: EXTENDS / IMPLEMENTS / annotations / members ---
    for finfo in file_infos:
        for decl in finfo.type_decls:
            type_id = type_node_id[decl.fqn]
            _emit_supertypes(graph, decl, finfo, index, type_id)
            _emit_annotations(graph, decl.node, finfo.src, type_id)
            _emit_members(graph, decl, finfo, index, type_id, type_methods)

    # --- Pass 2c: CALLS + OVERRIDES (needs type_methods fully populated) ---
    for finfo in file_infos:
        for decl in finfo.type_decls:
            _emit_calls_and_overrides(graph, decl, finfo, index, type_methods)

    return graph


def _emit_supertypes(graph: Graph, decl: TypeDeclInfo, finfo: FileInfo, index: RepoIndex, type_id: str) -> None:
    for raw, _resolved, is_superclass in _collect_supertypes(decl, finfo, index):
        if is_superclass:
            target_id, _, _ = _ensure_type_node(graph, raw, index, finfo.file_path, finfo.package, "CLASS")
            graph.add_rel(type_id, target_id, "EXTENDS")
        else:
            target_id, _, _ = _ensure_type_node(graph, raw, index, finfo.file_path, finfo.package, "INTERFACE")
            rel = "EXTENDS" if decl.kind == "Interface" else "IMPLEMENTS"
            graph.add_rel(type_id, target_id, rel)


def _emit_annotations(graph: Graph, decl_or_member_node: Node, src: bytes, owner_id: str) -> None:
    for ann in _annotation_nodes(decl_or_member_node):
        name, value = _annotation_name_and_value(ann, src)
        ann_id = f"annotation:{name}"
        if not graph.has_node(ann_id):
            graph.add_node(ann_id, "ANNOTATION_TYPE", name, name, external=True)
        graph.add_rel(owner_id, ann_id, "HAS_ANNOTATION", value=value or "")


def _emit_members(graph: Graph, decl: TypeDeclInfo, finfo: FileInfo, index: RepoIndex,
                   type_id: str, type_methods: dict[str, list[tuple[str, int, str]]]) -> None:
    src = finfo.src
    body = decl.node.child_by_field_name("body")
    if body is None:
        return
    for member in body.named_children:
        if member.type == "field_declaration":
            _emit_field(graph, member, finfo, index, type_id, decl)
        elif member.type == "constructor_declaration":
            _emit_constructor(graph, member, finfo, index, type_id, decl)
        elif member.type == "method_declaration":
            _emit_method(graph, member, finfo, index, type_id, decl, type_methods)


def _emit_field(graph: Graph, field_node: Node, finfo: FileInfo, index: RepoIndex, type_id: str, decl: TypeDeclInfo) -> None:
    src = finfo.src
    type_text = _text(src, field_node.child_by_field_name("type"))
    for declarator in field_node.children_by_field_name("declarator"):
        name_node = declarator.child_by_field_name("name")
        if name_node is None:
            continue
        fname = _text(src, name_node)
        field_id = f"field:{decl.fqn}.{fname}"
        graph.add_node(
            field_id, "FIELD", fname, f"{decl.fqn}.{fname}",
            visibility=_visibility(field_node, src),
            isStatic=_has_modifier_keyword(field_node, src, "static"),
            isFinal=_has_modifier_keyword(field_node, src, "final"),
            external=False,
        )
        graph.add_rel(type_id, field_id, "CONTAINS")
        _emit_annotations(graph, field_node, src, field_id)
        if type_text not in PRIMITIVE_TYPES:
            target_id, _, _ = _ensure_type_node(graph, type_text, index, finfo.file_path, finfo.package, "CLASS")
            graph.add_rel(field_id, target_id, "OF_TYPE")


def _emit_constructor(graph: Graph, ctor_node: Node, finfo: FileInfo, index: RepoIndex, type_id: str, decl: TypeDeclInfo) -> None:
    src = finfo.src
    params_node = ctor_node.child_by_field_name("parameters")
    params = _formal_params(params_node, src)
    ctor_id = _ctor_id(decl.fqn, len(params))
    graph.add_node(
        ctor_id, "CONSTRUCTOR", decl.simple_name, f"{decl.fqn}.<init>",
        visibility=_visibility(ctor_node, src), external=False,
    )
    graph.add_rel(type_id, ctor_id, "CONTAINS")
    _emit_annotations(graph, ctor_node, src, ctor_id)
    _emit_parameters(graph, ctor_id, params, index, finfo)


def _emit_parameters(graph: Graph, owner_id: str, params: list[tuple[str, str]], index: RepoIndex, finfo: FileInfo) -> None:
    for pname, ptype in params:
        param_id = f"param:{owner_id}.{pname}"
        graph.add_node(param_id, "PARAMETER", pname, pname, external=False)
        graph.add_rel(owner_id, param_id, "HAS_PARAMETER")
        if ptype not in PRIMITIVE_TYPES and ptype:
            target_id, _, _ = _ensure_type_node(graph, ptype, index, finfo.file_path, finfo.package, "CLASS")
            graph.add_rel(param_id, target_id, "OF_TYPE")


def _emit_method(graph: Graph, method_node: Node, finfo: FileInfo, index: RepoIndex, type_id: str,
                  decl: TypeDeclInfo, type_methods: dict[str, list[tuple[str, int, str]]]) -> None:
    src = finfo.src
    name_node = method_node.child_by_field_name("name")
    mname = _text(src, name_node)
    params_node = method_node.child_by_field_name("parameters")
    params = _formal_params(params_node, src)
    arity = len(params)
    method_id = _method_id(decl.fqn, mname, arity)

    return_type_node = method_node.child_by_field_name("type")
    return_type_text = _text(src, return_type_node)

    graph.add_node(
        method_id, "METHOD", mname, f"{decl.fqn}.{mname}",
        visibility=_visibility(method_node, src),
        isStatic=_has_modifier_keyword(method_node, src, "static"),
        isAbstract=_has_modifier_keyword(method_node, src, "abstract"),
        returnType=return_type_text,
        external=False,
    )
    graph.add_rel(type_id, method_id, "CONTAINS")
    type_methods.setdefault(decl.fqn, []).append((mname, arity, method_id))

    _emit_annotations(graph, method_node, src, method_id)
    _emit_parameters(graph, method_id, params, index, finfo)

    if return_type_node is not None and return_type_text not in PRIMITIVE_TYPES:
        target_id, _, _ = _ensure_type_node(graph, return_type_text, index, finfo.file_path, finfo.package, "CLASS")
        graph.add_rel(method_id, target_id, "RETURNS")

    for thrown in _throws_types(method_node, src):
        target_id, _, _ = _ensure_type_node(graph, thrown, index, finfo.file_path, finfo.package, "CLASS")
        graph.add_rel(method_id, target_id, "THROWS")


def _emit_calls_and_overrides(graph: Graph, decl: TypeDeclInfo, finfo: FileInfo, index: RepoIndex,
                               type_methods: dict[str, list[tuple[str, int, str]]]) -> None:
    src = finfo.src
    body = decl.node.child_by_field_name("body")
    if body is None:
        return

    supertypes = _collect_supertypes(decl, finfo, index)
    supertype_fqns = [fqn for _raw, fqn, _is_superclass in supertypes if fqn]
    # Raw supertype references that did NOT resolve to an internal type --
    # real-world @Override overwhelmingly targets framework/JDK interfaces
    # (Filter, MessageListener, CommandLineRunner, Object, ...), not other
    # classes in the same repo, so OVERRIDES needs an external fallback to be
    # useful at all (confirmed empirically: on a real Spring service, 5/5 of
    # the existing cmg-java engine's OVERRIDES edges target external methods).
    external_supertype_raws = [raw for raw, fqn, _is_superclass in supertypes if not fqn]

    for member in body.named_children:
        if member.type == "constructor_declaration":
            params = _formal_params(member.child_by_field_name("parameters"), src)
            ctor_id = _ctor_id(decl.fqn, len(params))
            ctx = MethodCallContext(
                caller_type_fqn=decl.fqn, caller_file=finfo.file_path, caller_package=finfo.package,
                local_types=_local_var_types(member.child_by_field_name("body"), src),
                field_types=_field_type_map(decl, finfo, src),
            )
            for pname, ptype in params:
                ctx.local_types.setdefault(pname, ptype)
            _emit_calls_in_body(graph, member.child_by_field_name("body"), src, ctor_id, ctx, index)

        elif member.type == "method_declaration":
            name_node = member.child_by_field_name("name")
            mname = _text(src, name_node)
            params = _formal_params(member.child_by_field_name("parameters"), src)
            arity = len(params)
            method_id = _method_id(decl.fqn, mname, arity)
            ctx = MethodCallContext(
                caller_type_fqn=decl.fqn, caller_file=finfo.file_path, caller_package=finfo.package,
                local_types=_local_var_types(member.child_by_field_name("body"), src),
                field_types=_field_type_map(decl, finfo, src),
            )
            for pname, ptype in params:
                ctx.local_types.setdefault(pname, ptype)
            _emit_calls_in_body(graph, member.child_by_field_name("body"), src, method_id, ctx, index)

            has_override = any(
                _annotation_name_and_value(ann, src)[0] == "Override"
                for ann in _annotation_nodes(member)
            )
            if has_override:
                matched = False
                for super_fqn in supertype_fqns:
                    for (sup_name, sup_arity, sup_id) in type_methods.get(super_fqn, []):
                        if sup_name == mname and sup_arity == arity:
                            graph.add_rel(method_id, sup_id, "OVERRIDES")
                            matched = True
                            break
                    if matched:
                        break
                if not matched and external_supertype_raws:
                    # No internal ancestor declares this method (or the only
                    # ancestor is external) -- target an external placeholder
                    # method under the first external supertype, mirroring
                    # what real symbol resolution actually produces.
                    raw = external_supertype_raws[0]
                    target_type_id, _, _ = _ensure_type_node(graph, raw, index, finfo.file_path, finfo.package, "CLASS")
                    target_type_name = graph.nodes[target_type_id]["qualifiedName"]
                    ext_method_id = f"method:external:{target_type_name}.{mname}/{arity}"
                    if not graph.has_node(ext_method_id):
                        graph.add_node(ext_method_id, "METHOD", mname, f"{target_type_name}.{mname}", external=True)
                        graph.add_rel(target_type_id, ext_method_id, "CONTAINS")
                    graph.add_rel(method_id, ext_method_id, "OVERRIDES")


def _field_type_map(decl: TypeDeclInfo, finfo: FileInfo, src: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    body = decl.node.child_by_field_name("body")
    if body is None:
        return result
    for member in body.named_children:
        if member.type != "field_declaration":
            continue
        type_text = _text(src, member.child_by_field_name("type"))
        for declarator in member.children_by_field_name("declarator"):
            name_node = declarator.child_by_field_name("name")
            if name_node is not None:
                result[_text(src, name_node)] = type_text
    return result
