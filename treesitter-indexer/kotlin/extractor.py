"""
Structural tree-sitter Kotlin extractor: AST -> node/rel graph targeting
lumen's existing canonical vocabulary (graph_store.py), mirroring
php/extractor.py's shape, ported from the old PSI-based
indexer/app/.../KotlinSourceParser.java's structural model (read directly
during planning, not mechanically translated).

Kotlin has NO explicit `this.`/`self::` requirement for calling the
enclosing class's own method -- a bare `inner()` call inside a method body
implicitly means "call this class's own method inner" (verified empirically
during planning). This is the single most important divergence from every
other language's CALLS resolution; see kotlin/calls.py's
_resolve_bare_call.

Packages map to Package nodes (same role as Java's). A class's primary
constructor is modeled as a Constructor node (id suffix ".<init>", same
convention php/extractor.py and the old Java/Kotlin engines both use); a
`val`/`var` primary-constructor parameter ALSO becomes a Field (promoted
property, same semantics as PHP's constructor property promotion).

Class kind -> table mapping (checked in this priority order, matching
`determineClassNodeType` in the old KotlinSourceParser.java): `data class`
-> DataClass; `sealed class` -> SealedClass; `sealed interface` ->
SealedInterface; plain `interface` -> Interface; otherwise -> Class.
`object` declarations -> ObjectDecl; a `companion object` nested inside a
class/interface body -> CompanionObject (a DIFFERENT node type than
ObjectDecl, with a COMPANION_OF edge back to its enclosing container).

A function is classified (priority order): has a receiver type (`fun
String.shout()`) -> ExtensionFunction; has the `suspend` modifier ->
SuspendFunction; otherwise Method (inside a class/object/interface body) or
Function (top-level). This mirrors the old engine's isExtension/isSuspend
priority exactly.

Annotation USAGE (not just declaration) is read directly off every
class/method/constructor declaration's `modifiers` child and emitted as
ordinary HAS_ANNOTATION edges -- the single highest-value new piece of this
increment (the old Kotlin parser never did this at all, see the design
spec). An annotation with no call arguments (`@Injectable`) parses as
`annotation -> user_type`; one with arguments (`@GetMapping("/x")`) parses
as `annotation -> constructor_invocation -> user_type` + `value_arguments`
-- both shapes are handled.

Sealed-subtype detection (SEALED_SUBTYPE) and class-level delegation
(DELEGATES_TO) both reuse the SAME heritage-walk code that resolves
EXTENDS/IMPLEMENTS -- a top-level sealed subtype (`class Foo : Parent()`)
parses with the exact same `delegation_specifiers -> delegation_specifier
-> constructor_invocation` shape as ordinary inheritance, so no separate
mechanism is needed; SEALED_SUBTYPE is additionally emitted whenever the
resolved supertype is internally known to be a SealedClass/SealedInterface.
Class-level "by" delegation (`class D(h: Helper) : Helper by h`) parses as
a DIFFERENT node shape (`delegation_specifier -> explicit_delegation`) and
emits DELEGATES_TO instead of EXTENDS/IMPLEMENTS. Property-level delegation
(`val x by lazy { ... }`) delegates to an EXPRESSION, not a type, and is
deliberately NOT modeled as an edge at all (same as the old engine, which
only sets a flag) -- marked via the existing generic `type` column
(`type: "delegated"`) on the Field node, the same "reuse the generic type
column" convention PHP's trait/constant-marking already established,
rather than adding a new boolean property that the write-allowlist would
silently drop.

Known, accepted divergences (documented, not "fixed" mid-port, same spirit
as every other language's extractor's own divergence list): nested
sealed-subtype declarations (`sealed class R { object Success : R() }`,
subtype declared INSIDE the sealed parent's own body) are not specially
handled -- only TOP-LEVEL, separately-declared sealed subtypes are
resolved, matching this increment's fixture and test coverage; local
(function-body-scoped) classes/objects are not extracted; `when`
expressions used as Kotlin's pattern-matching equivalent are not analyzed
for exhaustiveness or control flow (no CPG/statement-level graph at all in
this increment, same scope boundary as Python/PHP/JS).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dc_field

from tree_sitter import Language, Node, Parser
import tree_sitter_kotlin as tsk

from graph_model import Graph
from http_url_detection import looks_like_url
from kotlin.calls import ImportedName, RepoIndex, constructor_id, callable_id

KOTLIN_LANGUAGE = Language(tsk.language())

_EXCLUDED_DIRS = {
    ".git", "build", ".gradle", "test", "tests", ".idea",
}

_CLASS_MODIFIER_DATA = "data"
_CLASS_MODIFIER_SEALED = "sealed"
_FUNCTION_MODIFIER_SUSPEND = "suspend"


def _text(src: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node: Node | None, src: bytes) -> str | None:
    """Kotlin string literals wrap their content in a `string_content`
    child -- same shape as PHP's grammar, verified empirically (different
    from JS/TS's `string_fragment`)."""
    if node is None or node.type != "string_literal":
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return _text(src, content) if content is not None else ""


def _has_class_modifier(modifiers_node: Node | None, keyword: str) -> bool:
    if modifiers_node is None:
        return False
    for mod in modifiers_node.named_children:
        if mod.type != "class_modifier":
            continue
        # The keyword itself (`data`, `sealed`, ...) is an UNNAMED
        # (anonymous) token -- verified empirically -- so it shows up in
        # `.children`, NOT `.named_children`, which is always empty here.
        inner = mod.children[0] if mod.children else None
        if inner is not None and inner.type == keyword:
            return True
    return False


def _has_function_modifier(modifiers_node: Node | None, keyword: str) -> bool:
    if modifiers_node is None:
        return False
    for mod in modifiers_node.named_children:
        if mod.type != "function_modifier":
            continue
        # Same unnamed-token shape as _has_class_modifier above.
        inner = mod.children[0] if mod.children else None
        if inner is not None and inner.type == keyword:
            return True
    return False


def _annotation_name_and_value(annotation_node: Node, src: bytes) -> tuple[str, str | None] | None:
    """Returns (name, firstStringArgOrNone). An annotation with no
    arguments parses as `annotation -> user_type`; one with arguments
    parses as `annotation -> constructor_invocation -> user_type` +
    `value_arguments` -- both verified empirically during planning."""
    inner = next((c for c in annotation_node.named_children if c.type in ("user_type", "constructor_invocation")), None)
    if inner is None:
        return None
    if inner.type == "user_type":
        name_node = next((c for c in inner.named_children if c.type == "identifier"), None)
        return (_text(src, name_node), None) if name_node is not None else None
    user_type = next((c for c in inner.named_children if c.type == "user_type"), None)
    if user_type is None:
        return None
    name_node = next((c for c in user_type.named_children if c.type == "identifier"), None)
    if name_node is None:
        return None
    name = _text(src, name_node)
    args_node = next((c for c in inner.named_children if c.type == "value_arguments"), None)
    value = None
    if args_node is not None:
        for arg in args_node.named_children:
            if arg.type != "value_argument":
                continue
            inner_arg = arg.named_children[0] if arg.named_children else None
            text = _string_literal_text(inner_arg, src)
            if text is not None:
                value = text
                break
    return name, value


def _emit_annotations(graph: Graph, modifiers_node: Node | None, target_id: str, src: bytes) -> None:
    if modifiers_node is None:
        return
    for mod in modifiers_node.named_children:
        if mod.type != "annotation":
            continue
        result = _annotation_name_and_value(mod, src)
        if result is None:
            continue
        name, value = result
        ann_id = f"annotationtype:{name}"
        if not graph.has_node(ann_id):
            graph.add_node(ann_id, "AnnotationType", name, name, external=True, language="kotlin")
        graph.add_rel(target_id, ann_id, "HAS_ANNOTATION", value=value or "", language="kotlin")


def _class_table(modifiers_node: Node | None, is_interface: bool) -> str:
    if _has_class_modifier(modifiers_node, _CLASS_MODIFIER_DATA):
        return "DataClass"
    if _has_class_modifier(modifiers_node, _CLASS_MODIFIER_SEALED):
        return "SealedInterface" if is_interface else "SealedClass"
    return "Interface" if is_interface else "Class"


@dataclass
class ClassInfo:
    node: Node
    simple_name: str
    fqn: str
    table: str  # "Class" | "DataClass" | "SealedClass" | "Interface" | "SealedInterface"


@dataclass
class ObjectInfo:
    node: Node
    simple_name: str
    fqn: str
    is_companion: bool
    companion_of_fqn: str | None  # set only for companion objects


@dataclass
class CallableInfo:
    node: Node
    simple_name: str
    fqn: str
    table: str  # "Function" | "ExtensionFunction" | "SuspendFunction"
    receiver_type_text: str | None  # set only for ExtensionFunction


@dataclass
class FileInfo:
    file_path: str
    package: str
    src: bytes
    top_level_nodes: list = dc_field(default_factory=list)
    classes: list[ClassInfo] = dc_field(default_factory=list)
    objects: list[ObjectInfo] = dc_field(default_factory=list)
    callables: list[CallableInfo] = dc_field(default_factory=list)


def _package_of_file(tree_root: Node, src: bytes) -> str:
    header = next((c for c in tree_root.children if c.type == "package_header"), None)
    if header is None:
        return ""
    qn_node = next((c for c in header.named_children if c.type == "qualified_identifier"), None)
    return _text(src, qn_node) if qn_node is not None else ""


def _function_table_and_receiver(node: Node, src: bytes) -> tuple[str, str | None]:
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    name_field = node.child_by_field_name("name")
    # An extension function's receiver type is the user_type/identifier
    # sequence BEFORE the `name` field -- positional, no field name exists
    # for it (verified empirically).
    receiver_text: str | None = None
    if name_field is not None:
        for child in node.children:
            if child == name_field:
                break
            if child.type == "user_type":
                receiver_text = _text(src, child)
    if receiver_text is not None:
        return "ExtensionFunction", receiver_text
    if _has_function_modifier(modifiers, _FUNCTION_MODIFIER_SUSPEND):
        return "SuspendFunction", None
    return "Function", None


def _top_level_scan(file_path: str, src: bytes, tree_root: Node) -> FileInfo:
    package = _package_of_file(tree_root, src)
    info = FileInfo(file_path, package, src)
    info.top_level_nodes = list(tree_root.children)

    def qualify(simple: str) -> str:
        return f"{package}.{simple}" if package else simple

    for child in tree_root.children:
        if child.type == "class_declaration":
            name_node = child.child_by_field_name("name")
            simple = _text(src, name_node)
            modifiers = next((c for c in child.children if c.type == "modifiers"), None)
            is_interface = any(c.type == "interface" for c in child.children)
            table = _class_table(modifiers, is_interface)
            info.classes.append(ClassInfo(child, simple, qualify(simple), table))
        elif child.type == "object_declaration":
            name_node = child.child_by_field_name("name")
            simple = _text(src, name_node)
            info.objects.append(ObjectInfo(child, simple, qualify(simple), False, None))
        elif child.type == "function_declaration":
            name_node = child.child_by_field_name("name")
            simple = _text(src, name_node)
            table, receiver = _function_table_and_receiver(child, src)
            info.callables.append(CallableInfo(child, simple, qualify(simple), table, receiver))

    return info


def _build_repo_index(file_infos: list[FileInfo]) -> RepoIndex:
    index = RepoIndex()
    for finfo in file_infos:
        index.package_file.setdefault(finfo.package, finfo.file_path)
        index.package_functions.setdefault(finfo.package, set())
        for cls in finfo.classes:
            index.internal_classes.add(cls.fqn)
            index.class_file[cls.fqn] = finfo.file_path
            index.class_methods.setdefault(cls.fqn, {})
            index.class_table[cls.fqn] = cls.table
        for fn in finfo.callables:
            index.package_functions[finfo.package].add(fn.simple_name)
    return index


def _resolve_class_name(simple_or_qualified: str, package: str, index: RepoIndex) -> str | None:
    """No `import`-based aliasing attempted for type references (an
    accepted scope boundary, same posture as every other language's
    best-effort resolution) -- tries same-package, then a bare match
    against any internal class by simple name (best-effort, syntactic
    only)."""
    if simple_or_qualified.startswith("."):
        simple_or_qualified = simple_or_qualified[1:]
    same_pkg = f"{package}.{simple_or_qualified}" if package else simple_or_qualified
    if same_pkg in index.internal_classes:
        return same_pkg
    for fqn in index.internal_classes:
        if fqn == simple_or_qualified or fqn.endswith(f".{simple_or_qualified}"):
            return fqn
    return None


_HTTP_DEP_COUNTER = {"n": 0}


def _emit_http_dependency(graph: Graph, owner_id: str, key: str, value: str) -> None:
    _HTTP_DEP_COUNTER["n"] += 1
    qualified_name = f"code:{owner_id}#{key}#{_HTTP_DEP_COUNTER['n']}"
    prop_id = f"configprop:{qualified_name}"
    graph.add_node(prop_id, "ConfigProperty", key, qualified_name, code=value, external=False, language="kotlin")
    graph.add_rel(owner_id, prop_id, "REFERENCES_CONFIG", language="kotlin")


def _url_literal_args(call_node: Node, src: bytes) -> list[str]:
    args = next((c for c in call_node.named_children if c.type == "value_arguments"), None)
    if args is None:
        return []
    out = []
    for arg in args.named_children:
        if arg.type != "value_argument":
            continue
        inner = arg.named_children[0] if arg.named_children else None
        text = _string_literal_text(inner, src)
        if text is not None and looks_like_url(text):
            out.append(text)
    return out


_PRIMITIVE_TYPES = {
    "Int", "Long", "Short", "Byte", "Double", "Float", "Boolean", "Char",
    "String", "Unit", "Any", "Nothing",
}


def _ensure_type_node(graph: Graph, type_text: str, package: str, index: RepoIndex) -> str | None:
    simple = type_text.strip().lstrip("?")
    if not simple or simple in _PRIMITIVE_TYPES:
        return None
    resolved = _resolve_class_name(simple, package, index)
    if resolved:
        table = index.class_table.get(resolved, "Class")
        return f"{table.lower()}:{resolved}"
    ext_id = f"class:external:{simple}"
    if not graph.has_node(ext_id):
        graph.add_node(ext_id, "Class", simple, simple, external=True, language="kotlin")
    return ext_id


def _ensure_type_node_allow_primitive(graph: Graph, type_text: str, package: str, index: RepoIndex) -> str | None:
    """Same as _ensure_type_node but does NOT skip primitive types --
    needed for EXTENSION_OF, since Kotlin extension functions are
    overwhelmingly defined on built-in types (`fun String.shout()`, `fun
    Int.double()`), unlike OF_TYPE/RETURNS/HAS_PARAMETER where a
    primitive's type is uninteresting to model as its own node."""
    simple = type_text.strip().lstrip("?")
    if not simple:
        return None
    if simple in _PRIMITIVE_TYPES:
        ext_id = f"class:external:{simple}"
        if not graph.has_node(ext_id):
            graph.add_node(ext_id, "Class", simple, simple, external=True, language="kotlin")
        return ext_id
    return _ensure_type_node(graph, type_text, package, index)


def _emit_parameters(graph: Graph, owner_id: str, params_node: Node | None, src: bytes,
                      package: str, index: RepoIndex, class_id: str | None, class_fqn: str | None) -> None:
    """class_id/class_fqn are non-None only for a primary constructor --
    a `val`/`var` parameter is ALSO promoted to a Field (property
    promotion), same semantics as PHP's constructor property promotion."""
    if params_node is None:
        return
    param_type_name = "class_parameter" if class_id is not None else "parameter"
    for p in params_node.named_children:
        if p.type != param_type_name:
            continue
        # Neither class_parameter nor parameter field-name their children
        # (verified empirically) -- scan positionally.
        name_node = None
        type_node = None
        is_val_or_var = False
        for c in p.children:
            if c.type == "identifier" and name_node is None:
                name_node = c
            elif c.type == "user_type":
                type_node = c
            elif c.type in ("val", "var"):
                is_val_or_var = True
        if name_node is None:
            continue
        name = _text(src, name_node)
        type_text = _text(src, type_node) if type_node is not None else ""

        param_id = f"param:{owner_id}.{name}"
        graph.add_node(param_id, "Parameter", name, name, external=False, language="kotlin")
        graph.add_rel(owner_id, param_id, "HAS_PARAMETER", language="kotlin")
        target_id = _ensure_type_node(graph, type_text, package, index) if type_text else None
        if target_id:
            graph.add_rel(param_id, target_id, "OF_TYPE", language="kotlin")

        if is_val_or_var and class_id is not None and class_fqn is not None:
            field_id = f"field:{class_fqn}.{name}"
            graph.add_node(field_id, "Field", name, f"{class_fqn}.{name}", external=False, language="kotlin")
            graph.add_rel(class_id, field_id, "CONTAINS", language="kotlin")
            if target_id:
                graph.add_rel(field_id, target_id, "OF_TYPE", language="kotlin")


def _emit_return_type(graph: Graph, callable_node: Node, c_id: str, src: bytes, package: str, index: RepoIndex) -> None:
    # The return-type user_type sits between the final `)` of the
    # parameter list and the function_body -- found by locating the `:`
    # child (if present) and reading the next named sibling.
    found_colon = False
    for c in callable_node.children:
        if c.type == ":":
            found_colon = True
            continue
        if found_colon and c.type == "user_type":
            type_text = _text(src, c)
            target_id = _ensure_type_node(graph, type_text, package, index)
            if target_id:
                graph.add_rel(c_id, target_id, "RETURNS", language="kotlin")
            return
        if found_colon and c.type == "function_body":
            return


def _emit_heritage(graph: Graph, class_node: Node, class_id: str, class_fqn: str, class_table: str,
                    package: str, index: RepoIndex, src: bytes) -> None:
    """Resolves EXTENDS/IMPLEMENTS/SEALED_SUBTYPE/DELEGATES_TO -- all from
    the SAME `delegation_specifiers` child, since Kotlin uses one syntax
    (`: Supertype1, Supertype2`) for both class inheritance and interface
    implementation (the distinction is which TABLE the resolved target
    lands in, same approach every other language's heritage-resolution
    code already uses)."""
    specifiers = next((c for c in class_node.children if c.type == "delegation_specifiers"), None)
    if specifiers is None:
        return
    for spec in specifiers.named_children:
        if spec.type != "delegation_specifier":
            continue
        inner = spec.named_children[0] if spec.named_children else None
        if inner is None:
            continue
        if inner.type == "explicit_delegation":
            # Class-level "by" delegation: `class D(h: Helper) : Helper by h`.
            user_type = next((c for c in inner.named_children if c.type == "user_type"), None)
            if user_type is None:
                continue
            type_text = _text(src, user_type)
            target_id = _ensure_type_node(graph, type_text, package, index)
            if target_id:
                graph.add_rel(class_id, target_id, "DELEGATES_TO", language="kotlin")
            continue

        if inner.type == "constructor_invocation":
            user_type = next((c for c in inner.named_children if c.type == "user_type"), None)
        elif inner.type == "user_type":
            user_type = inner
        else:
            continue
        if user_type is None:
            continue
        type_text = _text(src, user_type)
        resolved_fqn = _resolve_class_name(type_text, package, index)
        target_table = index.class_table.get(resolved_fqn, "Class") if resolved_fqn else "Class"
        target_id = (f"{target_table.lower()}:{resolved_fqn}" if resolved_fqn
                     else _ensure_type_node(graph, type_text, package, index))
        if target_id is None:
            continue
        rel_type = "IMPLEMENTS" if target_table in ("Interface", "SealedInterface") else "EXTENDS"
        graph.add_rel(class_id, target_id, rel_type, language="kotlin")
        if target_table in ("SealedClass", "SealedInterface"):
            graph.add_rel(target_id, f"{class_table.lower()}:{class_fqn}", "SEALED_SUBTYPE", language="kotlin")


def _emit_class_body_member(graph: Graph, member: Node, src: bytes, container_id: str, container_fqn: str,
                             package: str, index: RepoIndex) -> tuple[str, Node] | None:
    if member.type == "function_declaration":
        name_node = member.child_by_field_name("name")
        name = _text(src, name_node)
        table, receiver = _function_table_and_receiver(member, src)
        m_table = "ExtensionFunction" if table == "ExtensionFunction" else ("SuspendFunction" if table == "SuspendFunction" else "Method")
        m_id = callable_id(f"{container_fqn}.{name}")
        graph.add_node(m_id, m_table, name, f"{container_fqn}.{name}", external=False, language="kotlin")
        graph.add_rel(container_id, m_id, "CONTAINS", language="kotlin")
        index.class_methods.setdefault(container_fqn, {})[name] = m_id
        modifiers = next((c for c in member.children if c.type == "modifiers"), None)
        _emit_annotations(graph, modifiers, m_id, src)
        params = next((c for c in member.children if c.type == "function_value_parameters"), None)
        _emit_parameters(graph, m_id, params, src, package, index, None, None)
        _emit_return_type(graph, member, m_id, src, package, index)
        body = member.child_by_field_name("body") or next((c for c in member.children if c.type == "function_body"), None)
        return m_id, body

    if member.type == "property_declaration":
        var_decl = next((c for c in member.named_children if c.type == "variable_declaration"), None)
        if var_decl is None:
            return None
        name_node = next((c for c in var_decl.named_children if c.type == "identifier"), None)
        if name_node is None:
            return None
        name = _text(src, name_node)
        type_node = next((c for c in var_decl.named_children if c.type == "user_type"), None)
        field_id = f"field:{container_fqn}.{name}"
        is_delegated = any(c.type == "property_delegate" for c in member.named_children)
        extra = {"type": "delegated"} if is_delegated else {}
        graph.add_node(field_id, "Field", name, f"{container_fqn}.{name}", external=False, language="kotlin", **extra)
        graph.add_rel(container_id, field_id, "CONTAINS", language="kotlin")
        if type_node is not None:
            target_id = _ensure_type_node(graph, _text(src, type_node), package, index)
            if target_id:
                graph.add_rel(field_id, target_id, "OF_TYPE", language="kotlin")
        return None

    return None


def extract_repo(repo_path: str) -> Graph:
    parser = Parser(KOTLIN_LANGUAGE)
    graph = Graph()

    kt_files: list[str] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        for fname in files:
            if fname.endswith((".kt", ".kts")):
                kt_files.append(os.path.join(root, fname))

    file_infos: list[FileInfo] = []
    for fpath in kt_files:
        with open(fpath, "rb") as fh:
            src = fh.read()
        tree = parser.parse(src)
        file_infos.append(_top_level_scan(fpath, src, tree.root_node))

    index = _build_repo_index(file_infos)

    # --- Pass 1: Package + Class/Object/Function nodes ---
    package_id_by_name: dict[str, str] = {}
    class_id_by_fqn: dict[str, str] = {}

    def package_id(pkg: str) -> str:
        if pkg not in package_id_by_name:
            pid = f"package:{pkg}"
            package_id_by_name[pkg] = pid
            graph.add_node(pid, "Package", pkg.rsplit(".", 1)[-1] if pkg else "", pkg, external=False, language="kotlin")
        return package_id_by_name[pkg]

    for finfo in file_infos:
        pkg_id = package_id(finfo.package)
        for cls in finfo.classes:
            class_id = f"{cls.table.lower()}:{cls.fqn}"
            class_id_by_fqn[cls.fqn] = class_id
            graph.add_node(class_id, cls.table, cls.simple_name, cls.fqn, external=False, language="kotlin")
            graph.add_rel(pkg_id, class_id, "CONTAINS", language="kotlin")
        for fn in finfo.callables:
            f_id = callable_id(fn.fqn)
            graph.add_node(f_id, fn.table, fn.simple_name, fn.fqn, external=False, language="kotlin")
            graph.add_rel(pkg_id, f_id, "CONTAINS", language="kotlin")
            index.function_ids[fn.fqn] = f_id
            index.function_table_by_id[f_id] = fn.table
            if fn.table == "ExtensionFunction" and fn.receiver_type_text:
                target_id = _ensure_type_node_allow_primitive(graph, fn.receiver_type_text, finfo.package, index)
                if target_id:
                    graph.add_rel(f_id, target_id, "EXTENSION_OF", language="kotlin")

    # --- Pass 2: EXTENDS/IMPLEMENTS/SEALED_SUBTYPE/DELEGATES_TO, members, annotations ---
    for finfo in file_infos:
        src = finfo.src
        for cls in finfo.classes:
            class_id = class_id_by_fqn[cls.fqn]
            modifiers = next((c for c in cls.node.children if c.type == "modifiers"), None)
            _emit_annotations(graph, modifiers, class_id, src)
            _emit_heritage(graph, cls.node, class_id, cls.fqn, cls.table, finfo.package, index, src)

            primary_ctor = next((c for c in cls.node.children if c.type == "primary_constructor"), None)
            if primary_ctor is not None:
                ctor_id = constructor_id(cls.fqn)
                graph.add_node(ctor_id, "Constructor", "<init>", f"{cls.fqn}.<init>", external=False, language="kotlin")
                graph.add_rel(class_id, ctor_id, "CONTAINS", language="kotlin")
                params = next((c for c in primary_ctor.children if c.type == "class_parameters"), None)
                _emit_parameters(graph, ctor_id, params, src, finfo.package, index, class_id, cls.fqn)

            body = next((c for c in cls.node.children if c.type == "class_body"), None)
            if body is None:
                continue
            for member in body.named_children:
                if member.type == "companion_object":
                    name_node = member.child_by_field_name("name")
                    simple = _text(src, name_node) if name_node is not None else "Companion"
                    comp_fqn = f"{cls.fqn}.{simple}"
                    comp_id = f"companionobject:{comp_fqn}"
                    graph.add_node(comp_id, "CompanionObject", simple, comp_fqn, external=False, language="kotlin")
                    graph.add_rel(comp_id, class_id, "COMPANION_OF", language="kotlin")
                    index.class_methods.setdefault(comp_fqn, {})
                    comp_body = next((c for c in member.children if c.type == "class_body"), None)
                    if comp_body is not None:
                        for comp_member in comp_body.named_children:
                            _emit_class_body_member(graph, comp_member, src, comp_id, comp_fqn, finfo.package, index)
                    continue
                _emit_class_body_member(graph, member, src, class_id, cls.fqn, finfo.package, index)

        for obj in finfo.objects:
            obj_id = f"objectdecl:{obj.fqn}"
            graph.add_node(obj_id, "ObjectDecl", obj.simple_name, obj.fqn, external=False, language="kotlin")
            graph.add_rel(package_id(finfo.package), obj_id, "CONTAINS", language="kotlin")
            index.class_methods.setdefault(obj.fqn, {})
            index.class_table[obj.fqn] = "ObjectDecl"
            index.internal_classes.add(obj.fqn)
            index.class_file[obj.fqn] = finfo.file_path
            modifiers = next((c for c in obj.node.children if c.type == "modifiers"), None)
            _emit_annotations(graph, modifiers, obj_id, src)
            _emit_heritage(graph, obj.node, obj_id, obj.fqn, "ObjectDecl", finfo.package, index, src)
            body = next((c for c in obj.node.children if c.type == "class_body"), None)
            if body is not None:
                for member in body.named_children:
                    _emit_class_body_member(graph, member, src, obj_id, obj.fqn, finfo.package, index)

        for fn in finfo.callables:
            f_id = index.function_ids[fn.fqn]
            params = next((c for c in fn.node.children if c.type == "function_value_parameters"), None)
            _emit_parameters(graph, f_id, params, src, finfo.package, index, None, None)
            _emit_return_type(graph, fn.node, f_id, src, finfo.package, index)

    # --- Pass 3: global simple-name index, then CALLS + Ktor routing ---
    for class_fqn, methods in index.class_methods.items():
        for mname, mid in methods.items():
            index.simple_callable_to_fqns.setdefault(mname, []).append(mid)
    for fqn, fid in index.function_ids.items():
        simple = fqn.rsplit(".", 1)[-1]
        index.simple_callable_to_fqns.setdefault(simple, []).append(fid)

    from kotlin.calls import emit_calls_and_routes_in_file  # local import: avoids a module-load cycle with resolve_call_route

    for finfo in file_infos:
        emit_calls_and_routes_in_file(graph, finfo, index)

    return graph
