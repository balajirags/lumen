"""
Wave A structural extractor: tree-sitter Python AST -> node/rel graph,
targeting lumen's existing canonical vocabulary (see graph_store.py), mirroring
java/extractor.py's shape and Pass 1/2 structure.

Mirrors indexer/parsers/python/parse.py's structural extraction (verified by
direct read of that file) for the parts that already work there (class/method/
field extraction, decorator capture), and deliberately closes three documented
gaps that file has (see the plan): function parameter/return-type hints were
never read at all (now Parameter/HAS_PARAMETER/RETURNS, mirroring Java);
CALLS only resolved `self.method()` plus a few narrow cases (now a 3-tier
confidence scheme -- see python/calls.py); Flask/FastAPI routing produced zero
Workflow traces since WorkflowBuilder.java has no Python strategy at all (now
postprocess/workflow_builder.py's build_python_workflows, fed by
python/decorators.py's decorator-signal extraction).

Known, accepted divergences from a full Python semantic model (documented,
not "fixed" mid-port, same spirit as java/extractor.py's own divergence list):
nested classes/functions are not modeled as their own CONTAINS members --
only module-level classes/functions and their immediate class-body methods
are walked, matching the CONTAINS schema's declared Module->Class/
Module->Function/Class->Method combos (there is no Class->Class or
Function->Function combo to target, mirroring Java's own nested-type
flattening); CALLS/decorator body-walks recurse into nested function bodies
unconditionally (a call inside a closure defined within a method is
attributed to the enclosing method), the same simplification Java's
_emit_calls_in_body makes; relative imports deeper than one leading dot
(`from ..pkg import x`) are not resolved; `async def` functions are typed as
AsyncFunction nodes but their CALLS/HAS_PARAMETER/RETURNS edges are only
declared in the shared schema for a Function/Method target (not each other),
so an async handler's outgoing calls/parameters can silently no-op exactly
like Java's pre-existing Enum/Record CONTAINS gaps -- this increment's own
fixture/tests deliberately exercise only plain (non-async) functions to stay
within the currently-declared schema combos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dc_field

from tree_sitter import Language, Node, Parser
import tree_sitter_python as tspython

from graph_model import Graph
from http_url_detection import looks_like_url
from python.calls import ImportedName, MethodCallContext, RepoIndex, function_id, method_id, resolve_call_target
from python.decorators import extract_decorator_signals

PYTHON_LANGUAGE = Language(tspython.language())

_EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    ".tox", "build", "dist", "site-packages", "test", "tests",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

# Builtins/typing-generic-wrapper names that don't warrant an OF_TYPE/RETURNS
# edge -- mirrors java/extractor.py's PRIMITIVE_TYPES exclusion.
PY_PRIMITIVE_TYPES = {
    "int", "float", "str", "bool", "bytes", "None", "dict", "list", "tuple",
    "set", "frozenset", "Any", "object",
}


def _text(src: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf8", errors="replace")


def _string_literal_text(node: Node | None, src: bytes) -> str | None:
    if node is None or node.type != "string":
        return None
    content = next((c for c in node.named_children if c.type == "string_content"), None)
    return _text(src, content) if content is not None else ""


def _detect_source_root(repo_path: str) -> str:
    """Python has no src/main/java-equivalent mandatory convention -- this
    only recognizes the one common opt-in layout (a top-level src/ containing
    .py files, e.g. Poetry's default); everything else walks the repo root
    itself. Unlike repo_layout.detect_source_root (Java/Gradle-specific:
    build.gradle/pom.xml-based module detection), there is no build-file
    signal Python repos reliably declare, so _EXCLUDED_DIRS below carries the
    exclusion burden instead of a source-root heuristic."""
    src_candidate = os.path.join(repo_path, "src")
    if os.path.isdir(src_candidate):
        for _root, _dirs, files in os.walk(src_candidate):
            if any(f.endswith(".py") for f in files):
                return src_candidate
    return repo_path


def _module_name(file_path: str, source_root: str) -> str:
    rel = os.path.relpath(file_path, source_root)
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts = [p for p in rel.split(os.sep) if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or os.path.splitext(os.path.basename(file_path))[0]


def _imports(tree_root: Node, src: bytes, module: str) -> dict[str, ImportedName]:
    """Kind is "unknown" for from-imports until _finalize_imports resolves it
    against a fully-built RepoIndex (a single file's imports can't tell
    class from function from re-exported-symbol on their own)."""
    result: dict[str, ImportedName] = {}
    package = module.rsplit(".", 1)[0] if "." in module else ""

    for child in tree_root.children:
        if child.type == "import_statement":
            for c in child.children:
                if c.type == "dotted_name":
                    dotted = _text(src, c)
                    result[dotted.split(".")[0]] = ImportedName("module", dotted)
                elif c.type == "aliased_import":
                    dotted = _text(src, c.child_by_field_name("name"))
                    alias = _text(src, c.child_by_field_name("alias"))
                    result[alias] = ImportedName("module", dotted)

        elif child.type == "import_from_statement":
            module_name_node = child.child_by_field_name("module_name")
            if module_name_node is None:
                continue
            if module_name_node.type == "relative_import":
                # Only a single leading dot ("from . import x") is resolved
                # -- see module docstring.
                if _text(src, module_name_node).count(".") != 1:
                    continue
                from_module = package
            else:
                from_module = _text(src, module_name_node)

            for name_node in child.children_by_field_name("name"):
                if name_node.type == "wildcard_import":
                    continue
                if name_node.type == "aliased_import":
                    orig = _text(src, name_node.child_by_field_name("name"))
                    alias = _text(src, name_node.child_by_field_name("alias"))
                else:
                    orig = _text(src, name_node)
                    alias = orig
                target = f"{from_module}.{orig}" if from_module else orig
                result[alias] = ImportedName("unknown", target)

    return result


@dataclass
class ClassInfo:
    node: Node
    simple_name: str
    fqn: str
    decorators: list[Node] = dc_field(default_factory=list)


@dataclass
class FunctionInfo:
    node: Node
    simple_name: str
    fqn: str
    is_async: bool
    decorators: list[Node] = dc_field(default_factory=list)


@dataclass
class FileInfo:
    file_path: str
    src: bytes
    module: str
    imports: dict[str, ImportedName]
    classes: list[ClassInfo] = dc_field(default_factory=list)
    functions: list[FunctionInfo] = dc_field(default_factory=list)
    # Module-level `var = ClassName(...)` assignments (raw call nodes,
    # resolved to class fqns only after imports are finalized -- e.g. Flask's
    # `repo = UserRepository()` at module scope, read (without `global`) from
    # inside a route handler function below it).
    module_level_ctor_calls: list[tuple[str, Node]] = dc_field(default_factory=list)
    module_var_classes: dict[str, str] = dc_field(default_factory=dict)


def _decorators_and_inner(node: Node) -> tuple[list[Node], Node]:
    """node is either the bare definition, or a decorated_definition wrapping
    one -- returns (decoratorNodes, innerDefinitionNode)."""
    if node.type == "decorated_definition":
        named = node.named_children
        decorators = [c for c in named if c.type == "decorator"]
        inner = named[-1]
        return decorators, inner
    return [], node


def _pass1_scan_file(file_path: str, src: bytes, tree_root: Node, source_root: str) -> FileInfo:
    module = _module_name(file_path, source_root)
    imports = _imports(tree_root, src, module)
    info = FileInfo(file_path, src, module, imports)

    for top in tree_root.named_children:
        decorators, inner = _decorators_and_inner(top)
        if inner.type == "class_definition":
            name = _text(src, inner.child_by_field_name("name"))
            info.classes.append(ClassInfo(inner, name, f"{module}.{name}" if module else name, decorators))
        elif inner.type == "function_definition":
            name = _text(src, inner.child_by_field_name("name"))
            is_async = inner.children[0].type == "async"
            info.functions.append(FunctionInfo(inner, name, f"{module}.{name}" if module else name, is_async, decorators))
        elif top.type == "expression_statement":
            assign = next((c for c in top.named_children if c.type == "assignment"), None)
            if assign is not None:
                left = assign.child_by_field_name("left")
                right = assign.child_by_field_name("right")
                if left is not None and left.type == "identifier" and right is not None and right.type == "call":
                    info.module_level_ctor_calls.append((_text(src, left), right))

    return info


def _build_repo_index(file_infos: list[FileInfo]) -> RepoIndex:
    index = RepoIndex()
    for finfo in file_infos:
        index.internal_modules.add(finfo.module)
        index.module_file[finfo.module] = finfo.file_path
        index.module_functions.setdefault(finfo.module, set())
        for cls in finfo.classes:
            index.internal_classes.add(cls.fqn)
            index.class_file[cls.fqn] = finfo.file_path
            index.class_methods.setdefault(cls.fqn, {})
        for fn in finfo.functions:
            index.module_functions[finfo.module].add(fn.simple_name)
    return index


def _finalize_imports(file_infos: list[FileInfo], index: RepoIndex) -> None:
    for finfo in file_infos:
        for local, imp in list(finfo.imports.items()):
            if imp.kind != "unknown":
                continue
            if imp.target in index.internal_classes:
                finfo.imports[local] = ImportedName("class", imp.target)
                continue
            module, _sep, func = imp.target.rpartition(".")
            if func in index.module_functions.get(module, ()):
                finfo.imports[local] = ImportedName("function", imp.target)


def _resolve_class_reference(func_node: Node | None, src: bytes, module: str,
                              imports: dict[str, ImportedName], index: RepoIndex) -> str | None:
    """Only a bare `ClassName(...)` call is treated as a constructor
    reference (an accepted simplification -- `pkg.ClassName(...)` isn't)."""
    if func_node is None or func_node.type != "identifier":
        return None
    name = _text(src, func_node)
    same_module = index.resolve_class_in_module(module, name)
    if same_module:
        return same_module
    imported = imports.get(name)
    if imported and imported.kind == "class":
        return imported.target
    return None


def _local_var_classes(body_node: Node | None, src: bytes, module: str,
                        imports: dict[str, ImportedName], index: RepoIndex) -> dict[str, str]:
    """Best-effort scan for `var = ClassName(...)` assignments anywhere in a
    function/method body (Wave A scope only, mirrors java/extractor.py's
    _local_var_types) -- closes the documented `repo = UserRepository();
    repo.find_by_id(...)` CALLS gap."""
    result: dict[str, str] = {}
    if body_node is None:
        return result

    def walk(n: Node):
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            if left is not None and left.type == "identifier" and right is not None and right.type == "call":
                class_fqn = _resolve_class_reference(right.child_by_field_name("function"), src, module, imports, index)
                if class_fqn:
                    result[_text(src, left)] = class_fqn
        for child in n.children:
            walk(child)

    walk(body_node)
    return result


def _url_literal_args(call_node: Node, src: bytes) -> list[str]:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return []
    out = []
    for arg in args.named_children:
        node = arg.child_by_field_name("value") if arg.type == "keyword_argument" else arg
        text = _string_literal_text(node, src) if node is not None else None
        if text is not None and looks_like_url(text):
            out.append(text)
    return out


_HTTP_DEP_COUNTER = {"n": 0}


def _emit_http_dependency(graph: Graph, owner_id: str, key: str, value: str) -> None:
    """Mirrors java/extractor.py's _emit_http_dependency -- reuses the same
    ConfigProperty/REFERENCES_CONFIG shape so get_external_dependencies()
    needs no changes to surface these."""
    _HTTP_DEP_COUNTER["n"] += 1
    qualified_name = f"code:{owner_id}#{key}#{_HTTP_DEP_COUNTER['n']}"
    prop_id = f"configprop:{qualified_name}"
    graph.add_node(prop_id, "ConfigProperty", key, qualified_name, code=value, external=False, language="python")
    graph.add_rel(owner_id, prop_id, "REFERENCES_CONFIG", language="python")


_DECORATOR_COUNTER = {"n": 0}


def _emit_decorators(graph: Graph, decorator_nodes: list[Node], src: bytes, owner_id: str) -> None:
    for dec in decorator_nodes:
        for name, value in extract_decorator_signals(dec, src):
            _DECORATOR_COUNTER["n"] += 1
            dec_id = f"decorator:{owner_id}#{name}#{_DECORATOR_COUNTER['n']}"
            graph.add_node(dec_id, "Decorator", name, name, external=True, language="python")
            graph.add_rel(dec_id, owner_id, "DECORATES", value=value or "", language="python")


def _annotation_type_text(type_node: Node | None, src: bytes) -> str:
    if type_node is None:
        return ""
    inner = type_node.named_children[0] if type_node.named_children else None
    return _text(src, inner if inner is not None else type_node)


def _strip_generic(type_text: str) -> str:
    """Best-effort strip of a typing generic wrapper, e.g. "Optional[str]" ->
    "str", "List[Item]" -> "Item" -- takes the FIRST inner argument only, the
    same simplification java/calls.py's RepoIndex.resolve_type_name makes for
    Java's `<...>` generics."""
    text = type_text.strip()
    if "[" in text and text.endswith("]"):
        inner = text[text.index("[") + 1: -1]
        first = inner.split(",")[0].strip()
        return first or text
    return text


def _ensure_type_node(graph: Graph, type_text: str, module: str,
                       imports: dict[str, ImportedName], index: RepoIndex) -> str | None:
    simple = _strip_generic(type_text)
    if not simple or simple in PY_PRIMITIVE_TYPES:
        return None
    resolved = index.resolve_class_in_module(module, simple)
    if resolved is None:
        imported = imports.get(simple)
        if imported and imported.kind == "class":
            resolved = imported.target
    if resolved:
        return f"class:{resolved}"
    ext_id = f"class:external:{simple}"
    if not graph.has_node(ext_id):
        graph.add_node(ext_id, "Class", simple, simple, external=True, language="python")
    return ext_id


def _emit_parameters(graph: Graph, owner_id: str, params_node: Node | None, src: bytes,
                      module: str, imports: dict[str, ImportedName], index: RepoIndex) -> None:
    if params_node is None:
        return
    for p in params_node.named_children:
        if p.type == "identifier":
            name, type_text = _text(src, p), ""
        elif p.type == "typed_parameter":
            name_node = next((c for c in p.children if c.type == "identifier"), None)
            name = _text(src, name_node)
            type_text = _annotation_type_text(p.child_by_field_name("type"), src)
        elif p.type == "default_parameter":
            name, type_text = _text(src, p.child_by_field_name("name")), ""
        elif p.type == "typed_default_parameter":
            name = _text(src, p.child_by_field_name("name"))
            type_text = _annotation_type_text(p.child_by_field_name("type"), src)
        elif p.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            name_node = next((c for c in p.children if c.type == "identifier"), None)
            name, type_text = _text(src, name_node), ""
        else:
            continue

        if name in ("self", "cls"):
            continue

        param_id = f"param:{owner_id}.{name}"
        graph.add_node(param_id, "Parameter", name, name, external=False, language="python")
        graph.add_rel(owner_id, param_id, "HAS_PARAMETER", language="python")
        if type_text:
            target_id = _ensure_type_node(graph, type_text, module, imports, index)
            if target_id:
                graph.add_rel(param_id, target_id, "OF_TYPE", language="python")


def _contains_yield(node: Node) -> bool:
    """Stops at nested function_definition boundaries so an inner closure's
    yield doesn't misclassify the outer function as a generator."""
    if node.type in ("yield", "yield_expression"):
        return True
    for child in node.children:
        if child.type == "function_definition":
            continue
        if _contains_yield(child):
            return True
    return False


def _emit_field(graph: Graph, assign_node: Node, src: bytes, class_id: str, module: str,
                 imports: dict[str, ImportedName], index: RepoIndex, class_fqn: str) -> None:
    left = assign_node.child_by_field_name("left")
    if left is None or left.type != "identifier":
        return
    fname = _text(src, left)
    field_id = f"field:{class_fqn}.{fname}"
    graph.add_node(field_id, "Field", fname, f"{class_fqn}.{fname}", external=False, language="python")
    graph.add_rel(class_id, field_id, "CONTAINS", language="python")
    type_text = _annotation_type_text(assign_node.child_by_field_name("type"), src)
    if type_text:
        target_id = _ensure_type_node(graph, type_text, module, imports, index)
        if target_id:
            graph.add_rel(field_id, target_id, "OF_TYPE", language="python")


def _emit_method(graph: Graph, method_node: Node, decorators: list[Node], src: bytes, class_id: str,
                  class_fqn: str, module: str, imports: dict[str, ImportedName], index: RepoIndex) -> str:
    name = _text(src, method_node.child_by_field_name("name"))
    m_id = method_id(class_fqn, name)
    graph.add_node(m_id, "Method", name, f"{class_fqn}.{name}", external=False, language="python")
    graph.add_rel(class_id, m_id, "CONTAINS", language="python")
    index.class_methods.setdefault(class_fqn, {})[name] = m_id

    _emit_decorators(graph, decorators, src, m_id)
    _emit_parameters(graph, m_id, method_node.child_by_field_name("parameters"), src, module, imports, index)
    return_type_node = method_node.child_by_field_name("return_type")
    if return_type_node is not None:
        target_id = _ensure_type_node(graph, _annotation_type_text(return_type_node, src), module, imports, index)
        if target_id:
            graph.add_rel(m_id, target_id, "RETURNS", language="python")
    return m_id


def extract_repo(repo_path: str) -> Graph:
    parser = Parser(PYTHON_LANGUAGE)
    graph = Graph()
    source_root = _detect_source_root(repo_path)

    py_files: list[str] = []
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        for fname in files:
            if fname.endswith(".py"):
                py_files.append(os.path.join(root, fname))

    file_infos: list[FileInfo] = []
    for fpath in py_files:
        with open(fpath, "rb") as fh:
            src = fh.read()
        tree = parser.parse(src)
        file_infos.append(_pass1_scan_file(fpath, src, tree.root_node, source_root))

    index = _build_repo_index(file_infos)
    _finalize_imports(file_infos, index)
    for finfo in file_infos:
        for var_name, call_node in finfo.module_level_ctor_calls:
            class_fqn = _resolve_class_reference(
                call_node.child_by_field_name("function"), finfo.src, finfo.module, finfo.imports, index,
            )
            if class_fqn:
                finfo.module_var_classes[var_name] = class_fqn

    # --- Pass 2a: Module + Class + top-level Function nodes ---
    class_id_by_fqn: dict[str, str] = {}
    for finfo in file_infos:
        module_id = f"module:{finfo.module}"
        if not graph.has_node(module_id):
            graph.add_node(module_id, "Module", finfo.module or os.path.basename(finfo.file_path),
                            finfo.module, external=False, language="python")

        for cls in finfo.classes:
            class_id = f"class:{cls.fqn}"
            class_id_by_fqn[cls.fqn] = class_id
            graph.add_node(class_id, "Class", cls.simple_name, cls.fqn, external=False, language="python")
            graph.add_rel(module_id, class_id, "CONTAINS", language="python")

        for fn in finfo.functions:
            src = finfo.src
            is_generator = _contains_yield(fn.node.child_by_field_name("body"))
            table = "AsyncFunction" if fn.is_async else ("Generator" if is_generator else "Function")
            f_id = function_id(finfo.module, fn.simple_name)
            graph.add_node(f_id, table, fn.simple_name, fn.fqn, external=False, language="python")
            graph.add_rel(module_id, f_id, "CONTAINS", language="python")
            index.function_ids[fn.fqn] = f_id

    # --- Pass 2b: members (fields/methods/decorators/parameters/returns) ---
    for finfo in file_infos:
        src = finfo.src
        for cls in finfo.classes:
            class_id = class_id_by_fqn[cls.fqn]
            _emit_decorators(graph, cls.decorators, src, class_id)
            body = cls.node.child_by_field_name("body")
            if body is None:
                continue
            for member in body.named_children:
                decorators, inner = _decorators_and_inner(member)
                if inner.type == "function_definition":
                    _emit_method(graph, inner, decorators, src, class_id, cls.fqn, finfo.module, finfo.imports, index)
                elif member.type == "expression_statement":
                    assign = next((c for c in member.named_children if c.type == "assignment"), None)
                    if assign is not None:
                        _emit_field(graph, assign, src, class_id, finfo.module, finfo.imports, index, cls.fqn)

        for fn in finfo.functions:
            f_id = index.function_ids[fn.fqn]
            _emit_decorators(graph, fn.decorators, src, f_id)
            _emit_parameters(graph, f_id, fn.node.child_by_field_name("parameters"), src, finfo.module, finfo.imports, index)
            return_type_node = fn.node.child_by_field_name("return_type")
            if return_type_node is not None:
                target_id = _ensure_type_node(graph, _annotation_type_text(return_type_node, src), finfo.module, finfo.imports, index)
                if target_id:
                    graph.add_rel(f_id, target_id, "RETURNS", language="python")

    # --- Pass 2c: build the global simple-name index (needs every Method/
    # Function id known), then CALLS ---
    for class_fqn, methods in index.class_methods.items():
        for mname, mid in methods.items():
            index.simple_callable_to_fqns.setdefault(mname, []).append(mid)
    for fqn, fid in index.function_ids.items():
        simple = fqn.rsplit(".", 1)[-1]
        index.simple_callable_to_fqns.setdefault(simple, []).append(fid)

    for finfo in file_infos:
        src = finfo.src
        for cls in finfo.classes:
            body = cls.node.child_by_field_name("body")
            if body is None:
                continue
            for member in body.named_children:
                _decorators, inner = _decorators_and_inner(member)
                if inner.type != "function_definition":
                    continue
                mname = _text(src, inner.child_by_field_name("name"))
                m_id = index.class_methods.get(cls.fqn, {}).get(mname)
                if m_id is None:
                    continue
                body_node = inner.child_by_field_name("body")
                # Module-level ctor assignments come first so a function-local
                # `var = Other()` correctly shadows a same-named module global.
                local_classes = {**finfo.module_var_classes,
                                  **_local_var_classes(body_node, src, finfo.module, finfo.imports, index)}
                ctx = MethodCallContext(
                    caller_module=finfo.module, caller_class=cls.fqn, caller_file=finfo.file_path,
                    imports=finfo.imports, local_var_classes=local_classes,
                )
                _emit_calls_in_body(graph, body_node, src, m_id, ctx, index, caller_label=f"{cls.simple_name}.{mname}")

        for fn in finfo.functions:
            f_id = index.function_ids[fn.fqn]
            body_node = fn.node.child_by_field_name("body")
            local_classes = {**finfo.module_var_classes,
                              **_local_var_classes(body_node, src, finfo.module, finfo.imports, index)}
            ctx = MethodCallContext(
                caller_module=finfo.module, caller_class=None, caller_file=finfo.file_path,
                imports=finfo.imports, local_var_classes=local_classes,
            )
            _emit_calls_in_body(graph, body_node, src, f_id, ctx, index, caller_label=fn.simple_name)

    return graph


def _split_call_function(func_node: Node | None, src: bytes) -> tuple[str | None, str | None]:
    """Returns (receiverTextOrNone, methodName), mirroring java/extractor.py's
    _receiver_text -- None receiver means a bare call (`helper()`); "self"
    means a self-scoped call; anything else is the raw receiver expression
    text (which may itself be a multi-hop chain like "self.repo", left as-is
    for resolve_call_target's global-fallback tier to attempt)."""
    if func_node is None:
        return None, None
    if func_node.type == "identifier":
        return None, _text(src, func_node)
    if func_node.type == "attribute":
        obj = func_node.child_by_field_name("object")
        attr = func_node.child_by_field_name("attribute")
        method_name = _text(src, attr)
        if obj is not None and obj.type == "identifier" and _text(src, obj) == "self":
            return "self", method_name
        return (_text(src, obj) if obj is not None else None), method_name
    return None, None


def _emit_calls_in_body(graph: Graph, body_node: Node | None, src: bytes, caller_id: str,
                         ctx: MethodCallContext, index: RepoIndex, caller_label: str = "") -> None:
    if body_node is None:
        return

    def walk(n: Node):
        if n.type == "call":
            func_node = n.child_by_field_name("function")
            receiver_text, method_name = _split_call_function(func_node, src)
            if method_name is not None:
                for url in _url_literal_args(n, src):
                    _emit_http_dependency(graph, caller_id, f"http-call:{caller_label}.{method_name}", url)
                result = resolve_call_target(receiver_text, method_name, ctx, index)
                if result is not None:
                    target_id, confidence, reason = result
                    line = n.start_point[0] + 1
                    graph.add_rel(caller_id, target_id, "CALLS", lineNumber=line,
                                  confidence=confidence, reason=reason, language="python")
        for child in n.children:
            walk(child)

    walk(body_node)
