"""
Structural tree-sitter JS/TS/TSX extractor: AST -> node/rel graph targeting
lumen's existing canonical vocabulary (graph_store.py), mirroring
php/extractor.py's shape, adapted for two grammars selected per file by
extension.

Modules map to Module nodes (one per file, no namespace layer -- JS/TS has
no Java/PHP-style namespace system; CONTAINS already declares Module as the
container for Function/ArrowFunction/Component/Hook/Class, confirmed by
reading graph_store.py during planning). Module qualifiedName is the file's
path relative to the repo root with its extension stripped and backslashes
normalized to forward slashes (e.g. "app/widget.js" -> "app/widget").

React component/hook detection is a pure naming heuristic applied at
emission time to top-level functions/arrow-functions/class-property-arrow-
functions, NOT JSX-return-type analysis (explicit, approved scope
boundary -- see the design spec): a PascalCase name -> Component; a name
matching /^use[A-Z]/ -> Hook; otherwise Function/ArrowFunction as normal.

A class's `constructor` method is modeled as a Constructor node (same
convention as php/extractor.py's `__construct`); every other class member
that is EITHER a `method_definition` OR a `public_field_definition`/
`field_definition` whose value is an `arrow_function` (the common React
class-component `handleClick = () => {...}` pattern) is modeled as a
Method, so its body gets walked for CALLS like any other method -- a
plain, non-arrow-function field stays a Field only.

Known, accepted divergences (documented, not "fixed" mid-port, same spirit
as every other language's extractor's own divergence list): `import`
resolution only follows relative paths (`./foo`, `../bar`) to another file
in the corpus -- package imports (`import X from 'pkg'`) are not resolved
at all, matching the explicit scope boundary against node_modules
resolution; `export default` and named exports are both unwrapped to their
inner declaration but the distinction itself (default vs named) is not
modeled; destructured imports (`import { a, b } from './x'`) are resolved
per-named-import, but a dynamic `require()`/`import()` call is not
recognized as an import at all.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field as dc_field

from tree_sitter import Language, Node, Parser
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts

from graph_model import Graph
from http_url_detection import looks_like_url
from js.calls import ImportedName, RepoIndex, constructor_id, callable_id

JS_LANGUAGE = Language(tsjs.language())
TS_LANGUAGE = Language(tsts.language_typescript())
TSX_LANGUAGE = Language(tsts.language_tsx())

_EXCLUDED_DIRS = {
    ".git", "node_modules", "dist", "build", "coverage", ".next",
    "tests", "test", "__tests__", ".idea",
}

_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
_TS_EXTENSION = ".ts"
_TSX_EXTENSION = ".tsx"

_COMPONENT_RE = re.compile(r"^[A-Z]")
_HOOK_RE = re.compile(r"^use[A-Z]")


def _language_for_path(path: str) -> Language:
    if path.endswith(_TSX_EXTENSION):
        return TSX_LANGUAGE
    if path.endswith(_TS_EXTENSION):
        return TS_LANGUAGE
    return JS_LANGUAGE


def _text(src: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node: Node | None, src: bytes) -> str | None:
    """JS/TS string literals wrap their content in a `string_fragment`
    child -- a different node TYPE than PHP's `string_content`, verified
    empirically during planning (both single- and double-quoted strings use
    the same `string` node type; template literals are a separate
    `template_string` type this engine does not read as a string literal,
    same "no interpolation" posture as every other language's string-value
    extractor)."""
    if node is None or node.type != "string":
        return None
    content = next((c for c in node.named_children if c.type == "string_fragment"), None)
    return _text(src, content) if content is not None else ""


def _callable_kind(name: str) -> str:
    """Naming-heuristic React detection (explicit, approved scope boundary
    -- no JSX-return-type analysis): PascalCase -> Component, use-prefixed
    -> Hook, else a plain Function/ArrowFunction (the caller decides which
    of those two based on syntax, this only decides the Component/Hook
    override)."""
    if _HOOK_RE.match(name):
        return "Hook"
    if _COMPONENT_RE.match(name):
        return "Component"
    return ""


def _module_qn(repo_path: str, file_path: str) -> str:
    rel = os.path.relpath(file_path, repo_path)
    rel = rel.replace(os.sep, "/")
    for ext in (*_JS_EXTENSIONS, _TS_EXTENSION, _TSX_EXTENSION):
        if rel.endswith(ext):
            return rel[: -len(ext)]
    return rel


@dataclass
class ClassInfo:
    node: Node
    simple_name: str
    fqn: str


@dataclass
class CallableInfo:
    node: Node          # function_declaration / arrow_function
    simple_name: str
    fqn: str
    table: str           # "Function" | "ArrowFunction" | "Component" | "Hook"


@dataclass
class FileInfo:
    file_path: str
    module_qn: str
    src: bytes
    language: Language
    imports: dict[str, ImportedName]
    top_level_nodes: list = dc_field(default_factory=list)
    classes: list[ClassInfo] = dc_field(default_factory=list)
    callables: list[CallableInfo] = dc_field(default_factory=list)


def _unwrap_export(node: Node) -> Node:
    """`export function foo() {}` / `export default class Foo {}` both wrap
    the real declaration in an export_statement's `declaration` field --
    unwrap it so the rest of the extractor never special-cases "exported"
    vs. not (this engine doesn't model the export/import boundary itself,
    only relative-import resolution for CALLS)."""
    if node.type == "export_statement":
        decl = node.child_by_field_name("declaration")
        if decl is not None:
            return decl
    return node


def _resolve_relative_import(source_text: str, importer_module_qn: str, repo_files: set[str]) -> str | None:
    """Resolves `./foo`/`../bar` against the importer's own module path,
    trying both the bare path and an `/index` suffix (the common
    directory-import convention) -- confirmed-missing in the OLD engine
    (backlog: "no directory index.js resolution"), closed here as a small,
    well-scoped improvement consistent with "fix real gaps found via actual
    testing" precedent, not scope creep (the design spec's only explicit
    OUT-of-scope import items are tsconfig path aliases and node_modules
    resolution -- directory-index relative resolution is neither)."""
    if not source_text.startswith("."):
        return None
    importer_dir = importer_module_qn.rsplit("/", 1)[0] if "/" in importer_module_qn else ""
    combined = os.path.normpath(os.path.join(importer_dir, source_text)).replace(os.sep, "/")
    for candidate in (combined, f"{combined}/index"):
        if candidate in repo_files:
            return candidate
    return None


def _imports(tree_root: Node, src: bytes) -> list[tuple[str, str, bool]]:
    """Returns (localName, sourceText, isDefault) triples for every
    import_statement at the top of the file -- resolved against the
    corpus's actual module set in a second pass (_build_repo_index), since
    not every file has been scanned yet when a single file's imports are
    first read."""
    out: list[tuple[str, str, bool]] = []
    for child in tree_root.children:
        if child.type != "import_statement":
            continue
        source_node = child.child_by_field_name("source")
        source_text = _string_literal_text(source_node, src) or ""
        clause = next((c for c in child.named_children if c.type == "import_clause"), None)
        if clause is None:
            continue
        for c in clause.named_children:
            if c.type == "named_imports":
                for spec in c.named_children:
                    if spec.type != "import_specifier":
                        continue
                    name_node = spec.child_by_field_name("name") or next(
                        (gc for gc in spec.named_children if gc.type == "identifier"), None)
                    if name_node is not None:
                        out.append((_text(src, name_node), source_text, False))
            elif c.type == "identifier":
                out.append((_text(src, c), source_text, True))
    return out


def _top_level_scan(file_path: str, module_qn: str, src: bytes, language: Language, tree_root: Node) -> FileInfo:
    raw_imports = _imports(tree_root, src)
    info = FileInfo(file_path, module_qn, src, language, {})
    info.top_level_nodes = list(tree_root.children)

    for child in tree_root.children:
        decl = _unwrap_export(child)
        if decl.type == "class_declaration":
            name_node = decl.child_by_field_name("name")
            simple = _text(src, name_node)
            info.classes.append(ClassInfo(decl, simple, f"{module_qn}.{simple}"))
        elif decl.type == "function_declaration":
            name_node = decl.child_by_field_name("name")
            simple = _text(src, name_node)
            table = _callable_kind(simple) or "Function"
            info.callables.append(CallableInfo(decl, simple, f"{module_qn}.{simple}", table))
        elif decl.type in ("lexical_declaration", "variable_declaration"):
            for declarator in decl.named_children:
                if declarator.type != "variable_declarator":
                    continue
                value = declarator.child_by_field_name("value")
                if value is None or value.type != "arrow_function":
                    continue
                name_node = declarator.child_by_field_name("name")
                simple = _text(src, name_node)
                table = _callable_kind(simple) or "ArrowFunction"
                info.callables.append(CallableInfo(value, simple, f"{module_qn}.{simple}", table))

    # raw_imports is attached to the FileInfo instance for the second pass
    # (_build_repo_index) to resolve against the full corpus; stashed as a
    # plain attribute rather than a dataclass field since it's a
    # build-time-only intermediate, not part of the public FileInfo shape
    # other modules read.
    info._raw_imports = raw_imports  # type: ignore[attr-defined]
    return info


def _build_repo_index(file_infos: list[FileInfo]) -> RepoIndex:
    index = RepoIndex()
    repo_files = {finfo.module_qn for finfo in file_infos}
    for finfo in file_infos:
        index.module_file[finfo.module_qn] = finfo.file_path
        index.module_classes.setdefault(finfo.module_qn, {})
        index.module_callables.setdefault(finfo.module_qn, {})
        for cls in finfo.classes:
            index.internal_classes.add(cls.fqn)
            index.class_file[cls.fqn] = finfo.file_path
            index.class_methods.setdefault(cls.fqn, {})
            index.module_classes[finfo.module_qn][cls.simple_name] = f"class:{cls.fqn}"
        for fn in finfo.callables:
            index.module_callables[finfo.module_qn][fn.simple_name] = callable_id(fn.fqn)

    for finfo in file_infos:
        imports: dict[str, ImportedName] = {}
        for local_name, source_text, _is_default in finfo._raw_imports:  # type: ignore[attr-defined]
            target_module = _resolve_relative_import(source_text, finfo.module_qn, repo_files)
            if target_module is not None:
                imports[local_name] = ImportedName(target_module)
        finfo.imports = imports

    return index


_PRIMITIVE_TYPES = {
    "string", "number", "boolean", "void", "any", "unknown", "never",
    "null", "undefined", "object", "symbol", "bigint",
}


def _type_text(type_annotation: Node | None, src: bytes) -> str:
    """Handles TS's `type_annotation` wrapper (`: Foo`, `: Array<User>`) --
    for a `generic_type`, captures only the base type name (e.g. "Array"),
    not its type arguments (explicit, approved scope boundary -- no full
    generic-parameter model)."""
    if type_annotation is None:
        return ""
    inner = type_annotation.named_children[0] if type_annotation.named_children else None
    if inner is None:
        return ""
    if inner.type == "generic_type":
        name_node = inner.child_by_field_name("name")
        return _text(src, name_node) if name_node is not None else ""
    return _text(src, inner)


def _ensure_type_node(graph: Graph, type_text: str) -> str | None:
    simple = type_text.strip()
    if not simple or simple in _PRIMITIVE_TYPES:
        return None
    ext_id = f"class:external:{simple}"
    if not graph.has_node(ext_id):
        graph.add_node(ext_id, "Class", simple, simple, external=True, language="js")
    return ext_id


def _emit_parameters(graph: Graph, owner_id: str, formal_params: Node | None, src: bytes) -> None:
    if formal_params is None:
        return
    for p in formal_params.named_children:
        if p.type == "required_parameter" or p.type == "optional_parameter":
            name_node = p.child_by_field_name("pattern")
            name = _text(src, name_node)
            type_text = _type_text(p.child_by_field_name("type"), src)
        elif p.type == "identifier":
            name = _text(src, p)
            type_text = ""
        else:
            continue
        if not name:
            continue
        param_id = f"param:{owner_id}.{name}"
        graph.add_node(param_id, "Parameter", name, name, external=False, language="js")
        graph.add_rel(owner_id, param_id, "HAS_PARAMETER", language="js")
        target_id = _ensure_type_node(graph, type_text) if type_text else None
        if target_id:
            graph.add_rel(param_id, target_id, "OF_TYPE", language="js")


def _class_params_and_return(graph: Graph, callable_node: Node, c_id: str, src: bytes) -> None:
    _emit_parameters(graph, c_id, callable_node.child_by_field_name("parameters"), src)
    return_type_node = callable_node.child_by_field_name("return_type")
    if return_type_node is not None:
        type_text = _type_text(return_type_node, src)
        target_id = _ensure_type_node(graph, type_text) if type_text else None
        if target_id:
            graph.add_rel(c_id, target_id, "RETURNS", language="js")


def _emit_class_member(graph: Graph, member: Node, src: bytes, class_id: str, class_fqn: str,
                        index: RepoIndex) -> tuple[str, Node] | None:
    """Returns (callableId, bodyNode) if this member is callable (a real
    method, or a field whose value is an arrow function -- the React
    `handleClick = () => {...}` pattern), else None (a plain, non-callable
    field). Decorators are NOT read here -- see js/calls.py's
    _emit_class_decorators, which pairs decorator nodes with the member
    immediately following them positionally (TS's class_body does not
    field-name a decorated member at all, verified empirically during
    planning -- field-name-based lookup would silently miss every
    decorated member)."""
    if member.type == "method_definition":
        name_node = member.child_by_field_name("name")
        name = _text(src, name_node)
        is_ctor = name == "constructor"
        m_id = constructor_id(class_fqn) if is_ctor else callable_id(f"{class_fqn}.{name}")
        table = "Constructor" if is_ctor else "Method"
        graph.add_node(m_id, table, name, f"{class_fqn}.{name}", external=False, language="js")
        graph.add_rel(class_id, m_id, "CONTAINS", language="js")
        if not is_ctor:
            index.class_methods.setdefault(class_fqn, {})[name] = m_id
        _class_params_and_return(graph, member, m_id, src)
        return m_id, member.child_by_field_name("body")

    if member.type in ("public_field_definition", "field_definition"):
        # Verified empirically: a class field's name is in the `property`
        # field, NOT `name` (unlike method_definition, which does use `name`).
        name_node = member.child_by_field_name("property")
        name = _text(src, name_node)
        value = member.child_by_field_name("value")
        if value is not None and value.type == "arrow_function":
            m_id = callable_id(f"{class_fqn}.{name}")
            graph.add_node(m_id, "Method", name, f"{class_fqn}.{name}", external=False, language="js")
            graph.add_rel(class_id, m_id, "CONTAINS", language="js")
            index.class_methods.setdefault(class_fqn, {})[name] = m_id
            _class_params_and_return(graph, value, m_id, src)
            return m_id, value.child_by_field_name("body")

        field_id = f"field:{class_fqn}.{name}"
        graph.add_node(field_id, "Field", name, f"{class_fqn}.{name}", external=False, language="js")
        graph.add_rel(class_id, field_id, "CONTAINS", language="js")
        type_text = _type_text(member.child_by_field_name("type"), src)
        if type_text:
            target_id = _ensure_type_node(graph, type_text)
            if target_id:
                graph.add_rel(field_id, target_id, "OF_TYPE", language="js")
        return None

    return None


def _emit_heritage(graph: Graph, class_node: Node, class_id: str, src: bytes) -> None:
    """JS's class_heritage is a flat `extends <identifier>`; TS's nests
    extends_clause (field `value`)/implements_clause inside class_heritage
    -- verified empirically during planning that these are genuinely
    different shapes between the two grammars, not just a cosmetic
    difference, so both are handled explicitly rather than assuming one
    covers the other."""
    heritage = next((c for c in class_node.children if c.type == "class_heritage"), None)
    if heritage is None:
        return
    extends_clause = next((c for c in heritage.children if c.type == "extends_clause"), None)
    if extends_clause is not None:
        value = extends_clause.child_by_field_name("value")
        if value is not None:
            target_id = _ensure_type_node(graph, _text(src, value))
            if target_id:
                graph.add_rel(class_id, target_id, "EXTENDS", language="js")
        implements_clause = next((c for c in heritage.children if c.type == "implements_clause"), None)
        if implements_clause is not None:
            for c in implements_clause.named_children:
                if c.type == "type_identifier":
                    target_id = _ensure_type_node(graph, _text(src, c))
                    if target_id:
                        graph.add_rel(class_id, target_id, "IMPLEMENTS", language="js")
        return

    # Plain JS shape: class_heritage's own children are `extends` + a bare
    # identifier, no nested extends_clause at all.
    base_node = next((c for c in heritage.named_children if c.type == "identifier"), None)
    if base_node is not None:
        target_id = _ensure_type_node(graph, _text(src, base_node))
        if target_id:
            graph.add_rel(class_id, target_id, "EXTENDS", language="js")


def extract_repo(repo_path: str) -> Graph:
    graph = Graph()

    js_files: list[str] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        for fname in files:
            if fname.endswith(_JS_EXTENSIONS) or fname.endswith(_TS_EXTENSION) or fname.endswith(_TSX_EXTENSION):
                js_files.append(os.path.join(root, fname))

    file_infos: list[FileInfo] = []
    for fpath in js_files:
        with open(fpath, "rb") as fh:
            src = fh.read()
        language = _language_for_path(fpath)
        parser = Parser(language)
        tree = parser.parse(src)
        module_qn = _module_qn(repo_path, fpath)
        file_infos.append(_top_level_scan(fpath, module_qn, src, language, tree.root_node))

    index = _build_repo_index(file_infos)

    # --- Pass 1: Module + Class/Function/ArrowFunction/Component/Hook nodes ---
    module_id_by_qn: dict[str, str] = {}
    class_id_by_fqn: dict[str, str] = {}

    for finfo in file_infos:
        mod_id = f"module:{finfo.module_qn}"
        module_id_by_qn[finfo.module_qn] = mod_id
        graph.add_node(mod_id, "Module", finfo.module_qn.rsplit("/", 1)[-1], finfo.module_qn,
                       external=False, language="js", path=finfo.file_path)

        for cls in finfo.classes:
            class_id = f"class:{cls.fqn}"
            class_id_by_fqn[cls.fqn] = class_id
            graph.add_node(class_id, "Class", cls.simple_name, cls.fqn, external=False, language="js")
            graph.add_rel(mod_id, class_id, "CONTAINS", language="js")

        for fn in finfo.callables:
            f_id = callable_id(fn.fqn)
            graph.add_node(f_id, fn.table, fn.simple_name, fn.fqn, external=False, language="js")
            graph.add_rel(mod_id, f_id, "CONTAINS", language="js")

    # --- Pass 2: EXTENDS/IMPLEMENTS, class members, top-level callable params/returns ---
    for finfo in file_infos:
        src = finfo.src
        for cls in finfo.classes:
            class_id = class_id_by_fqn[cls.fqn]
            _emit_heritage(graph, cls.node, class_id, src)

            body = cls.node.child_by_field_name("body")
            if body is None:
                continue
            from js.calls import emit_class_members_with_decorators  # local import: see module-cycle note in js/calls.py
            emit_class_members_with_decorators(graph, body, src, class_id, cls.fqn, index,
                                                lambda m, s, cid, cfqn: _emit_class_member(graph, m, s, cid, cfqn, index))

        for fn in finfo.callables:
            f_id = callable_id(fn.fqn)
            _class_params_and_return(graph, fn.node, f_id, src)

    # --- Pass 3: global simple-name index, then CALLS + routing ---
    for class_fqn, methods in index.class_methods.items():
        for mname, mid in methods.items():
            index.simple_callable_to_fqns.setdefault(mname, []).append(mid)
    for module_qn, callables in index.module_callables.items():
        for fname, fid in callables.items():
            index.simple_callable_to_fqns.setdefault(fname, []).append(fid)

    from js.calls import emit_calls_and_routes_in_file  # local import: avoids a module-load cycle (see js/calls.py docstring)

    for finfo in file_infos:
        emit_calls_and_routes_in_file(graph, finfo, index)

    return graph


# Re-exported for js/calls.py's local imports (same pattern as
# php/extractor.py's _emit_route_annotation/_url_literal_args/
# _emit_http_dependency being imported back from php/calls.py).
def url_literal_args(call_node: Node, src: bytes) -> list[str]:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return []
    out = []
    for arg in args.named_children:
        text = _string_literal_text(arg, src)
        if text is not None and looks_like_url(text):
            out.append(text)
    return out


_HTTP_DEP_COUNTER = {"n": 0}


def emit_http_dependency(graph: Graph, owner_id: str, key: str, value: str) -> None:
    _HTTP_DEP_COUNTER["n"] += 1
    qualified_name = f"code:{owner_id}#{key}#{_HTTP_DEP_COUNTER['n']}"
    prop_id = f"configprop:{qualified_name}"
    graph.add_node(prop_id, "ConfigProperty", key, qualified_name, code=value, external=False, language="js")
    graph.add_rel(owner_id, prop_id, "REFERENCES_CONFIG", language="js")


_ANNOTATION_TYPE_CACHE: dict[str, str] = {}


def ensure_annotation_type(graph: Graph, name: str) -> str:
    ann_id = f"annotationtype:{name}"
    if not graph.has_node(ann_id):
        graph.add_node(ann_id, "AnnotationType", name, name, external=True, language="js")
    return ann_id


def emit_route_annotation(graph: Graph, target_id: str, http_method: str, http_path: str | None) -> None:
    verb_name = http_method.capitalize() if http_method != "HTTP" else "Mapping"
    ann_id = ensure_annotation_type(graph, verb_name)
    graph.add_rel(target_id, ann_id, "HAS_ANNOTATION", value=http_path or "", language="js")
