"""
Canonical graph store for the tree-sitter indexer prototype.

Schema is an adapted copy of indexer/parsers/python/store.py's KuzuStore schema
(not imported directly -- this prototype must stay decoupled from cmg-python's
release lifecycle and from the existing indexer/ tree entirely), plus:
  - Workflow / Domain node tables and WORKFLOW_STEP / IN_DOMAIN rel tables,
    ported from indexer/app/.../store/KuzuGraphStore.java, since this increment
    also ports WorkflowBuilder/DomainDetector post-processing.
  - CALLS.confidence DOUBLE + CALLS.reason STRING (Java's richer shape) instead
    of store.py's CALLS.resolved BOOLEAN, since postprocess/ consumes confidence
    values directly.

Runs against LadybugDB (`pip install ladybug`), not the archived `kuzu` package --
this is brand-new code with no legacy dependency on `kuzu`, so it never takes on
the EOL'd library in the first place. LadybugDB is a fork that preserves Kuzu's
embedded API/Cypher dialect (Database/Connection/.execute()), so this schema and
query shape are expected to carry over unchanged.
"""

from __future__ import annotations

import os
import sys


def _buffer_pool_size() -> int:
    max_bytes = 2 * 1024 ** 3
    min_bytes = 512 * 1024 ** 2
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        if total > 0:
            return max(min_bytes, min(int(total * 0.8), max_bytes))
    except Exception:
        pass
    return min_bytes


def _with_normalized_rel_props(definition: str) -> str:
    idx = definition.rfind(")")
    if idx == -1:
        return definition
    return definition[:idx] + ", language STRING, kind STRING, normKind STRING" + definition[idx:]


# Full cross-language vocabulary, matching indexer/parsers/python/store.py's
# NODE_TYPES, so this can become the single canonical writer once JS/Python/PHP
# are ported in later increments. Only the Java-relevant subset is populated by
# this increment's extractor.
NODE_TYPES = [
    "Package", "Class", "Interface", "Enum", "Record", "AnnotationType",
    "Method", "Constructor", "Field", "Parameter", "File", "Statement",
    "Module", "Function", "ArrowFunction", "Component", "Hook", "JSXElement",
    "Decorator", "Generator", "AsyncFunction", "Comprehension",
    "DataClass", "SealedClass", "SealedInterface", "ObjectDecl",
    "CompanionObject", "ExtensionFunction", "SuspendFunction",
    "Property", "Lambda", "InitBlock", "TypeAlias",
    "ConfigFile", "ConfigProperty",
]

# Same shape as store.py's REL_DEFINITIONS, except CALLS carries
# confidence/reason (Java's shape) instead of resolved BOOLEAN.
REL_DEFINITIONS = [
    "CREATE REL TABLE IF NOT EXISTS CONTAINS (FROM Package TO Class, FROM Package TO Interface, FROM Package TO Enum, FROM Package TO Record, FROM Package TO Function, FROM Class TO Method, FROM Class TO Constructor, FROM Class TO Field, FROM Interface TO Method, FROM Interface TO Field, FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO Component, FROM Module TO Hook, FROM Module TO Class, FROM Class TO Function, FROM Class TO ArrowFunction, FROM DataClass TO Method, FROM DataClass TO Property, FROM SealedClass TO Method, FROM SealedClass TO Property, FROM ObjectDecl TO Method, FROM ObjectDecl TO Property, FROM CompanionObject TO Method, FROM CompanionObject TO Property, FROM Class TO Property, FROM Class TO InitBlock, FROM Package TO TypeAlias, FROM Package TO ObjectDecl, FROM ConfigFile TO ConfigProperty)",
    "CREATE REL TABLE IF NOT EXISTS EXTENDS (FROM Class TO Class, FROM Interface TO Interface, FROM Component TO Class, FROM DataClass TO Class, FROM SealedClass TO Class)",
    "CREATE REL TABLE IF NOT EXISTS IMPLEMENTS (FROM Class TO Interface, FROM DataClass TO Interface, FROM SealedClass TO Interface, FROM ObjectDecl TO Interface)",
    # Method->Function / Constructor->Function added for PHP, which (unlike
    # Java) commonly calls free/global functions directly from inside a class
    # method (e.g. Laravel's redirect()/response()/view() helpers) -- the
    # original CALLS combos only ever had a Function on the FROM side, never
    # the TO side, from a Method/Constructor caller.
    #
    # Method/Function/Constructor->Constructor added for PHP's `new X()`
    # expression (PHP's "new" IS a call to __construct -- see php/calls.py's
    # resolve_call_target "new" case) -- a pre-existing, cross-language gap
    # surfaced by this: Constructor never appeared on CALLS's TO side at all
    # before, only its FROM side (a constructor calling other things), so
    # `new`-expression edges from an ordinary Method/Function/Constructor
    # were silently dropped by the same "unrecognized combo -> binder
    # exception -> swallowed" failure mode already fixed twice elsewhere in
    # this project.
    # FROM ArrowFunction TO ArrowFunction, FROM Component TO Component,
    # FROM Hook TO ArrowFunction added for JS/TS/React -- these are the
    # exact combos the old (pre-tree-sitter) JS engine's own documented
    # schema/emission mismatch bug tried to create and silently lost data
    # on (see backlog/treesitter-indexer-remaining-languages.md). The
    # remaining additions (Function->ArrowFunction, ArrowFunction->
    # Constructor, Component->ArrowFunction, Component->Hook, Hook->Hook,
    # Hook->Method, Component->Method, Function->Component) were found via
    # upfront audit during planning, not yet observed via a real failure --
    # JS code routinely calls across all of these shapes (a Component
    # calling a locally-defined helper ArrowFunction, a Hook calling
    # another Hook, etc.), so they're added now rather than discovered one
    # silent-failure at a time during fixture testing.
    "CREATE REL TABLE IF NOT EXISTS CALLS (FROM Method TO Method, FROM Method TO Function, FROM Method TO Constructor, FROM Constructor TO Method, FROM Constructor TO Function, FROM Constructor TO Constructor, FROM Function TO Function, FROM Function TO Method, FROM Function TO Constructor, FROM Function TO ArrowFunction, FROM Function TO Component, FROM ArrowFunction TO Function, FROM ArrowFunction TO Method, FROM ArrowFunction TO ArrowFunction, FROM ArrowFunction TO Constructor, FROM Component TO Function, FROM Component TO Component, FROM Component TO ArrowFunction, FROM Component TO Hook, FROM Component TO Method, FROM Hook TO Function, FROM Hook TO ArrowFunction, FROM Hook TO Hook, FROM Hook TO Method, FROM AsyncFunction TO Function, FROM ExtensionFunction TO Method, FROM ExtensionFunction TO Function, FROM SuspendFunction TO Method, FROM SuspendFunction TO Function, FROM Lambda TO Method, FROM Lambda TO Function, FROM InitBlock TO Method, lineNumber INT64, confidence DOUBLE, reason STRING)",
    # Function->Class/Interface/Enum/Record added for Python's top-level
    # functions (e.g. a Flask/FastAPI route handler's own return-type hint) --
    # the original set only covered Method, since the Java-only increment
    # never emits a top-level, non-method Function node.
    "CREATE REL TABLE IF NOT EXISTS RETURNS (FROM Method TO Class, FROM Method TO Interface, FROM Method TO Enum, FROM Method TO Record, FROM Function TO Class, FROM Function TO Interface, FROM Function TO Enum, FROM Function TO Record, FROM ExtensionFunction TO Class, FROM SuspendFunction TO Class)",
    "CREATE REL TABLE IF NOT EXISTS HAS_PARAMETER (FROM Method TO Parameter, FROM Constructor TO Parameter, FROM Function TO Parameter, FROM ArrowFunction TO Parameter, FROM ExtensionFunction TO Parameter, FROM SuspendFunction TO Parameter, FROM Lambda TO Parameter)",
    "CREATE REL TABLE IF NOT EXISTS OF_TYPE (FROM Field TO Class, FROM Field TO Interface, FROM Field TO Enum, FROM Field TO Record, FROM Parameter TO Class, FROM Parameter TO Interface, FROM Parameter TO Enum, FROM Parameter TO Record, FROM Property TO Class, FROM Property TO Interface)",
    # Function/ArrowFunction added for JS/TS -- an Express/Fastify route
    # handler (the target of a resolved resolve_call_route() call) is very
    # commonly a plain Function or ArrowFunction, not a class Method the
    # way every prior annotation-based language's routes have been.
    "CREATE REL TABLE IF NOT EXISTS HAS_ANNOTATION (FROM Class TO AnnotationType, FROM Interface TO AnnotationType, FROM Method TO AnnotationType, FROM Constructor TO AnnotationType, FROM Field TO AnnotationType, FROM Property TO AnnotationType, FROM ExtensionFunction TO AnnotationType, FROM Function TO AnnotationType, FROM ArrowFunction TO AnnotationType, value STRING)",
    "CREATE REL TABLE IF NOT EXISTS OVERRIDES (FROM Method TO Method, FROM ExtensionFunction TO Method)",
    "CREATE REL TABLE IF NOT EXISTS THROWS (FROM Method TO Class)",
    "CREATE REL TABLE IF NOT EXISTS SOURCE_FILE (FROM Class TO File, FROM Interface TO File, FROM Enum TO File, FROM Record TO File, FROM AnnotationType TO File, FROM Module TO File, FROM Component TO File, FROM Function TO File, FROM DataClass TO File, FROM SealedClass TO File, FROM SealedInterface TO File, FROM ObjectDecl TO File, FROM ExtensionFunction TO File)",
    "CREATE REL TABLE IF NOT EXISTS AST_CHILD (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM ArrowFunction TO Statement, FROM AsyncFunction TO Statement, FROM Generator TO Statement, ast_order INT64)",
    "CREATE REL TABLE IF NOT EXISTS CFG_NEXT (FROM Method TO Statement, FROM Constructor TO Statement, FROM Statement TO Statement, FROM Function TO Statement, FROM AsyncFunction TO Statement, FROM Generator TO Statement, backEdge BOOLEAN)",
    "CREATE REL TABLE IF NOT EXISTS DATA_FLOW (FROM Statement TO Statement, variable STRING)",
    "CREATE REL TABLE IF NOT EXISTS IMPORTS (FROM Module TO Module, importedName STRING, localName STRING)",
    "CREATE REL TABLE IF NOT EXISTS EXPORTS (FROM Module TO Function, FROM Module TO ArrowFunction, FROM Module TO Component, FROM Module TO Class)",
    "CREATE REL TABLE IF NOT EXISTS RENDERS (FROM Component TO Component, FROM Function TO Component, FROM ArrowFunction TO Component, lineNumber INT64)",
    "CREATE REL TABLE IF NOT EXISTS USES_HOOK (FROM Component TO Hook, FROM Function TO Hook, FROM ArrowFunction TO Hook, lineNumber INT64)",
    "CREATE REL TABLE IF NOT EXISTS PROP_DEPENDENCY (FROM Component TO Component)",
    # value STRING mirrors HAS_ANNOTATION.value -- lets Python's decorator
    # mechanism (@app.route("/x")) capture the same kind of single-attribute
    # payload Java's HAS_ANNOTATION already does, so postprocess/ can treat
    # both as one shape (see workflow_builder.py/domain_detector.py).
    "CREATE REL TABLE IF NOT EXISTS DECORATES (FROM Decorator TO Function, FROM Decorator TO Method, FROM Decorator TO Class, FROM Decorator TO AsyncFunction, value STRING)",
    "CREATE REL TABLE IF NOT EXISTS YIELDS (FROM Generator TO Class)",
    "CREATE REL TABLE IF NOT EXISTS EXTENSION_OF (FROM ExtensionFunction TO Class, FROM ExtensionFunction TO Interface, FROM ExtensionFunction TO DataClass)",
    "CREATE REL TABLE IF NOT EXISTS DELEGATES_TO (FROM Property TO Class, FROM Property TO Interface)",
    "CREATE REL TABLE IF NOT EXISTS SEALED_SUBTYPE (FROM SealedClass TO Class, FROM SealedClass TO DataClass, FROM SealedClass TO ObjectDecl, FROM SealedInterface TO Class, FROM SealedInterface TO Interface)",
    "CREATE REL TABLE IF NOT EXISTS COMPANION_OF (FROM CompanionObject TO Class, FROM CompanionObject TO DataClass, FROM CompanionObject TO SealedClass)",
    "CREATE REL TABLE IF NOT EXISTS SUSPENDS (FROM SuspendFunction TO SuspendFunction, FROM SuspendFunction TO Method)",
    # Links a @Value("${...}")-annotated Java field/parameter/method to the
    # ConfigProperty it reads (config/extractor.py's link_config_usages()), and
    # also links code-detected HTTP dependencies -- a @FeignClient-annotated
    # interface/class, or a Method/Constructor calling RestTemplate/WebClient/
    # HttpClient with a literal URL argument -- to a synthesized ConfigProperty
    # representing that dependency (see http_url_detection.py).
    # Function/ArrowFunction added for JS/TS -- HTTP-dependency detection
    # (java/http_dependencies.py's looks_like_url, reused via
    # http_url_detection.py) runs on any call's arguments regardless of the
    # enclosing callable's shape, and JS/TS commonly makes such calls from a
    # top-level Function/ArrowFunction, not just a Method/Constructor the
    # way every prior language's callers have been.
    "CREATE REL TABLE IF NOT EXISTS REFERENCES_CONFIG (FROM Field TO ConfigProperty, FROM Parameter TO ConfigProperty, FROM Method TO ConfigProperty, FROM Constructor TO ConfigProperty, FROM Interface TO ConfigProperty, FROM Class TO ConfigProperty, FROM Function TO ConfigProperty, FROM ArrowFunction TO ConfigProperty)",
]

# Ported from KuzuGraphStore.java -- created after the base node tables (Workflow/
# Domain are referenced by WORKFLOW_STEP/IN_DOMAIN below) but before those rel tables.
ABSTRACTION_NODE_DEFS = [
    "CREATE NODE TABLE IF NOT EXISTS Workflow (id STRING, name STRING, qualifiedName STRING, entryPointId STRING, terminalId STRING, stepCount INT64, type STRING, httpMethod STRING, httpPath STRING, language STRING, kind STRING, normKind STRING, PRIMARY KEY(id))",
    "CREATE NODE TABLE IF NOT EXISTS Domain (id STRING, name STRING, qualifiedName STRING, heuristicLabel STRING, cohesion DOUBLE, memberCount INT64, language STRING, kind STRING, normKind STRING, PRIMARY KEY(id))",
]

ABSTRACTION_REL_DEFS = [
    "CREATE REL TABLE IF NOT EXISTS WORKFLOW_STEP (FROM Method TO Workflow, FROM Function TO Workflow, FROM AsyncFunction TO Workflow, FROM ArrowFunction TO Workflow, FROM Component TO Workflow, FROM Hook TO Workflow, step INT64)",
    "CREATE REL TABLE IF NOT EXISTS IN_DOMAIN (FROM Class TO Domain, FROM Interface TO Domain, FROM Method TO Domain, FROM Component TO Domain, FROM Module TO Domain, FROM AsyncFunction TO Domain, FROM Function TO Domain, FROM ArrowFunction TO Domain)",
]

NODE_TYPE_MAP = {
    "PACKAGE": "Package", "CLASS": "Class", "INTERFACE": "Interface",
    "ENUM": "Enum", "RECORD": "Record", "ANNOTATION_TYPE": "AnnotationType",
    "METHOD": "Method", "CONSTRUCTOR": "Constructor", "FIELD": "Field",
    "PARAMETER": "Parameter", "FILE": "File", "STATEMENT": "Statement",
    "CONFIG_FILE": "ConfigFile", "CONFIG_PROPERTY": "ConfigProperty",
}


def node_type_to_table(node_type: str | None) -> str | None:
    if not node_type:
        return None
    return NODE_TYPE_MAP.get(node_type.upper(), node_type)


def _escape(s) -> str:
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


def infer_language(_node_type: str | None, default: str = "java") -> str:
    """Historical default is "java" since this was originally a Java-only
    engine -- `default` lets a per-run caller (e.g. parse_python.py) override
    it for an entire save() call instead of hardcoding a second language."""
    return default


def infer_norm_kind(node_type: str | None) -> str:
    normalized = str(node_type or "").upper()
    if normalized in {"PACKAGE", "MODULE"}:
        return "CodeUnit"
    if normalized in {"METHOD", "CONSTRUCTOR", "FUNCTION", "ARROW_FUNCTION", "ASYNC_FUNCTION",
                       "GENERATOR", "EXTENSION_FUNCTION", "SUSPEND_FUNCTION", "LAMBDA"}:
        return "Callable"
    if normalized in {"CLASS", "INTERFACE", "ENUM", "RECORD", "DATA_CLASS", "SEALED_CLASS",
                       "SEALED_INTERFACE", "OBJECT_DECL", "TYPE_ALIAS"}:
        return "TypeLike"
    if normalized in {"FIELD", "PROPERTY", "PARAMETER"}:
        return "DataMember"
    if normalized == "FILE":
        return "SourceFile"
    if normalized in {"DECORATOR", "ANNOTATION_TYPE"}:
        return "AnnotationLike"
    if normalized == "STATEMENT":
        return "Statement"
    if normalized in {"CONFIG_FILE", "CONFIG_PROPERTY"}:
        return "ConfigLike"
    return "CodeElement"


def infer_rel_norm_kind(rel_type: str | None) -> str:
    normalized = str(rel_type or "").upper()
    if normalized in {"CONTAINS", "SOURCE_FILE"}:
        return "Contains"
    if normalized == "CALLS":
        return "Calls"
    if normalized in {"IMPORTS", "EXPORTS", "PROP_DEPENDENCY", "DELEGATES_TO"}:
        return "DependsOn"
    if normalized in {"EXTENDS", "IMPLEMENTS", "OVERRIDES", "SEALED_SUBTYPE", "COMPANION_OF",
                       "EXTENSION_OF"}:
        return "TypeRelation"
    if normalized in {"HAS_PARAMETER", "OF_TYPE", "RETURNS", "THROWS", "DECORATES",
                       "HAS_ANNOTATION", "YIELDS", "SUSPENDS"}:
        return "SemanticRelation"
    if normalized in {"AST_CHILD", "CFG_NEXT", "DATA_FLOW"}:
        return "FlowRelation"
    if normalized == "REFERENCES_CONFIG":
        return "DependsOn"
    return "CodeRelation"


_ALL_REL_TYPES = [
    "CONTAINS", "EXTENDS", "IMPLEMENTS", "CALLS", "RETURNS",
    "HAS_PARAMETER", "OF_TYPE", "HAS_ANNOTATION", "OVERRIDES", "THROWS",
    "SOURCE_FILE", "AST_CHILD", "CFG_NEXT", "DATA_FLOW",
    "IMPORTS", "EXPORTS", "RENDERS", "USES_HOOK", "PROP_DEPENDENCY",
    "DECORATES", "YIELDS",
    "EXTENSION_OF", "DELEGATES_TO", "SEALED_SUBTYPE", "COMPANION_OF", "SUSPENDS",
    "WORKFLOW_STEP", "IN_DOMAIN", "REFERENCES_CONFIG",
]


class LadybugGraphStore:
    """Canonical writer for the tree-sitter indexer, backed by LadybugDB."""

    def __init__(self, db_path: str):
        import ladybug as lb

        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            self.db = lb.Database(self.db_path, buffer_pool_size=_buffer_pool_size())
        except TypeError:
            # LadybugDB's constructor signature may not match Kuzu's exactly;
            # fall back to the minimal call shown in its own docs.
            self.db = lb.Database(self.db_path)
        self.conn = lb.Connection(self.db)
        print(f"LadybugDB: opening database at {self.db_path}", file=sys.stderr)

    def init_schema(self) -> None:
        for table in NODE_TYPES:
            self._query(
                f"CREATE NODE TABLE IF NOT EXISTS {table} "
                f"(id STRING, name STRING, qualifiedName STRING, "
                f"visibility STRING, isAbstract BOOLEAN, isStatic BOOLEAN, "
                f"isFinal BOOLEAN, returnType STRING, lineNumber INT64, "
                f"endLineNumber INT64, type STRING, external BOOLEAN, "
                f"path STRING, statementType STRING, code STRING, "
                f"inferred BOOLEAN, "
                f"language STRING, kind STRING, normKind STRING, "
                f"PRIMARY KEY (id))"
            )
        for node_def in ABSTRACTION_NODE_DEFS:
            self._query(node_def)
        for rel_def in REL_DEFINITIONS:
            self._query(_with_normalized_rel_props(rel_def))
        for rel_def in ABSTRACTION_REL_DEFS:
            self._query(_with_normalized_rel_props(rel_def))

    def clear(self) -> None:
        # DROP on a brand-new database (no tables yet) always "fails" --
        # that's expected on first run, not a real problem, so stay quiet.
        for rel in _ALL_REL_TYPES:
            self._query(f"DROP TABLE {rel}", quiet=True)
        for table in NODE_TYPES:
            self._query(f"DROP TABLE {table}", quiet=True)
        self._query("DROP TABLE Workflow", quiet=True)
        self._query("DROP TABLE Domain", quiet=True)
        self.init_schema()

    def save(self, graph_json: dict, language: str = "java") -> None:
        """`language` is the default for every node/rel in this graph_json
        that doesn't already carry an explicit `language` property -- lets a
        per-language entrypoint (parse.py vs parse_python.py) pass its own
        language once instead of every extractor stamping it on every node."""
        node_table_map: dict[str, str] = {}

        for node in graph_json["nodes"]:
            table = node_type_to_table(node["type"])
            node_table_map[node["id"]] = table

            cypher = (
                f"CREATE (n:{table} {{id: '{_escape(node['id'])}', "
                f"name: '{_escape(node['name'])}', "
                f"qualifiedName: '{_escape(node['qualifiedName'])}'"
            )

            props = {
                **node.get("properties", {}),
                "language": node.get("properties", {}).get("language", infer_language(node["type"], language)),
                "kind": table,
                "normKind": node.get("properties", {}).get("normKind", infer_norm_kind(node["type"])),
            }
            valid_keys = {
                "visibility", "isAbstract", "isStatic", "isFinal",
                "returnType", "lineNumber", "endLineNumber",
                "type", "external", "path", "inferred",
                "statementType", "code", "language", "kind", "normKind",
            }
            for key, val in props.items():
                if key not in valid_keys:
                    continue
                if isinstance(val, bool):
                    cypher += f", {key}: {'true' if val else 'false'}"
                elif isinstance(val, (int, float)):
                    cypher += f", {key}: {val}"
                else:
                    cypher += f", {key}: '{_escape(str(val))}'"

            cypher += "})"
            self._query(cypher)

        for rel in graph_json["relationships"]:
            src_table = node_table_map.get(rel["sourceId"])
            tgt_table = node_table_map.get(rel["targetId"])
            if not src_table or not tgt_table:
                continue

            props = {
                **rel.get("properties", {}),
                "language": rel.get("properties", {}).get("language", infer_language(rel.get("type"), language)),
                "kind": rel.get("type"),
                "normKind": infer_rel_norm_kind(rel.get("type")),
            }
            parts = []
            for key, val in props.items():
                if isinstance(val, bool):
                    parts.append(f"{key}: {'true' if val else 'false'}")
                elif isinstance(val, (int, float)):
                    parts.append(f"{key}: {val}")
                else:
                    parts.append(f"{key}: '{_escape(str(val))}'")
            rel_props = " {" + ", ".join(parts) + "}" if parts else ""

            cypher = (
                f"MATCH (a:{src_table} {{id: '{_escape(rel['sourceId'])}'}}),"
                f" (b:{tgt_table} {{id: '{_escape(rel['targetId'])}'}}) "
                f"CREATE (a)-[:{rel['type']}{rel_props}]->(b)"
            )
            self._query(cypher)

    def execute(self, cypher: str):
        return self.conn.execute(cypher)

    def close(self) -> None:
        pass

    def _query(self, cypher: str, quiet: bool = False) -> None:
        try:
            self.conn.execute(cypher)
        except Exception as exc:
            if not quiet:
                print(f"LadybugDB schema/write warning: {exc}", file=sys.stderr)
