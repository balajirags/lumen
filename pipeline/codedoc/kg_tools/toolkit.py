"""ReverseEngineerToolkit: 30+ graph query tools for code reverse-engineering."""

import json
import re
import sys
from pathlib import Path
from typing import Any

from codedoc.kg_tools.utils import (
    NODE_TYPES,
    REL_TYPES,
    _cypher_escape,
    _format_rows,
    _is_read_only,
)
from codedoc.kg_tools.registry import ToolRegistry


_NAME_TOKEN_BLACKLIST = {
    "api", "app", "client", "component", "context", "controller", "data", "default",
    "fetch", "function", "get", "handler", "hook", "http", "index", "layout", "module",
    "page", "post", "provider", "query", "request", "route", "router", "screen", "service",
    "state", "store", "tsx", "ts", "ui", "use", "view",
}


def _name_tokens(value: str) -> set[str]:
    if not value:
        return set()
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    parts = re.split(r"[^A-Za-z0-9]+", spaced)
    return {
        part.lower()
        for part in parts
        if len(part) >= 3 and part.lower() not in _NAME_TOKEN_BLACKLIST
    }


# ── Reverse Engineering Toolkit ─────────────────────────────────────────────


class ReverseEngineerToolkit:
    """
    All reverse-engineering graph query tools, exposed as plain Python methods.
    Each method returns a formatted string result — ready for LLM consumption
    or direct display.
    """

    def __init__(self, backend, repo_path: str = ""):
        self.backend = backend
        self.repo_path = repo_path
        self.registry = ToolRegistry()
        self._register_all_tools()

    # ── Exploration Tools ───────────────────────────────────────────────────

    def _register_all_tools(self):
        reg = self.registry
        backend = self.backend
        repo_path = self.repo_path

        # ------------------------------------------------------------------ #
        # query
        # ------------------------------------------------------------------ #
        @reg.tool()
        def query(cypher: str) -> str:
            """Run a read-only Cypher query against the code graph and return results.

            Examples:
              MATCH (c:Class) RETURN c.name, c.qualifiedName LIMIT 10
              MATCH (a)-[r:CALLS]->(b) RETURN a.qualifiedName, b.qualifiedName LIMIT 20
            """
            if not _is_read_only(cypher):
                return "Error: only read-only queries (MATCH/RETURN) are allowed."
            try:
                rows = backend.execute(cypher)
                return _format_rows(rows)
            except Exception as e:
                return f"Query error: {e}"

        # ------------------------------------------------------------------ #
        # get_schema
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_schema() -> str:
            """Get the graph schema: all node types, relationship types, and node properties."""
            schema = {
                "node_types": NODE_TYPES,
                "node_properties": [
                    "id", "name", "qualifiedName", "visibility",
                    "isAbstract", "isStatic", "isFinal", "returnType",
                    "lineNumber", "endLineNumber", "type", "external",
                    "path", "statementType", "code", "language", "kind",
                    "normKind",
                ],
                "relationship_types": REL_TYPES,
                "relationship_properties": {
                    "CALLS": ["lineNumber", "resolved"],
                    "RENDERS": ["lineNumber"],
                    "USES_HOOK": ["lineNumber"],
                    "AST_CHILD": ["ast_order"],
                    "CFG_NEXT": ["backEdge"],
                    "DATA_FLOW": ["variable"],
                    "IMPORTS": ["importedName", "localName"],
                    "*": ["language", "kind", "normKind"],
                },
            }
            return json.dumps(schema, indent=2)

        # ------------------------------------------------------------------ #
        # summary
        # ------------------------------------------------------------------ #
        @reg.tool()
        def summary() -> str:
            """Get a summary of the code graph: node counts per type and total relationships."""
            lines = []
            total_nodes = 0
            for t in NODE_TYPES:
                try:
                    rows = backend.execute(f"MATCH (n:{t}) RETURN count(n) AS c")
                    count = rows[0]["c"] if rows else 0
                    if count > 0:
                        lines.append(f"  {t}: {count}")
                        total_nodes += count
                except Exception:
                    pass
            total_rels = 0
            for r in REL_TYPES:
                try:
                    rows = backend.execute(f"MATCH ()-[r:{r}]->() RETURN count(r) AS c")
                    count = rows[0]["c"] if rows else 0
                    if count > 0:
                        lines.append(f"  {r}: {count}")
                        total_rels += count
                except Exception:
                    pass
            header = f"Nodes: {total_nodes}  |  Relationships: {total_rels}\n"
            return header + "\n".join(lines) if lines else "Graph is empty."

        # ------------------------------------------------------------------ #
        # search_nodes
        # ------------------------------------------------------------------ #
        @reg.tool()
        def search_nodes(name_pattern: str, node_type: str = "") -> str:
            """Search for nodes by name pattern (supports * wildcards) and optional type.

            Args:
                name_pattern: Name pattern to match. Use * as wildcard (e.g. '*Service*').
                node_type: Optional node type filter (e.g. 'Class', 'Method').
            """
            raw_terms = [term.lower() for term in name_pattern.split("*") if term]
            label = node_type if node_type in NODE_TYPES else None

            labels = [label] if label else [
                "Package", "Class", "Interface", "Method", "Function",
                "Module", "Component", "Field", "Constructor",
            ]
            try:
                rows: list[dict[str, Any]] = []
                for t in labels:
                    chunk = backend.execute(
                        f"MATCH (n:{t}) "
                        f"RETURN n.name AS name, n.qualifiedName AS qualifiedName, "
                        f"'{t}' AS type, n.language AS language, n.normKind AS normKind, "
                        f"n.lineNumber AS line, n.path AS path LIMIT 200"
                    )
                    rows.extend(chunk)
                filtered = []
                for row in rows:
                    haystack = " ".join(str(row.get(k, "") or "") for k in ("name", "qualifiedName")).lower()
                    if all(term in haystack for term in raw_terms):
                        filtered.append(row)
                return _format_rows(filtered[:50]) if filtered else "No results."
            except Exception as e:
                return f"Search error: {e}"

        # ------------------------------------------------------------------ #
        # get_callers
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_callers(method_name: str) -> str:
            """Find all callers of a given method or function."""
            esc = _cypher_escape(method_name)
            cypher = (
                f"MATCH (caller)-[r:CALLS]->(callee) "
                f"WHERE callee.name = '{esc}' OR callee.qualifiedName = '{esc}' "
                f"RETURN caller.qualifiedName AS caller, callee.qualifiedName AS callee, "
                f"r.lineNumber AS line LIMIT 50"
            )
            try:
                rows = backend.execute(cypher)
                return _format_rows(rows) if rows else f"No callers found for '{method_name}'."
            except Exception as e:
                return f"Error: {e}"

        # ------------------------------------------------------------------ #
        # get_callees
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_callees(method_name: str) -> str:
            """Find all methods/functions called by a given method or function."""
            esc = _cypher_escape(method_name)
            cypher = (
                f"MATCH (caller)-[r:CALLS]->(callee) "
                f"WHERE caller.name = '{esc}' OR caller.qualifiedName = '{esc}' "
                f"RETURN caller.qualifiedName AS caller, callee.qualifiedName AS callee, "
                f"r.lineNumber AS line LIMIT 50"
            )
            try:
                rows = backend.execute(cypher)
                return _format_rows(rows) if rows else f"No callees found for '{method_name}'."
            except Exception as e:
                return f"Error: {e}"

        # ------------------------------------------------------------------ #
        # get_class_hierarchy
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_class_hierarchy(class_name: str) -> str:
            """Get the inheritance hierarchy (EXTENDS/IMPLEMENTS) for a class or interface."""
            esc = _cypher_escape(class_name)
            lines = []
            for rel in ["EXTENDS", "IMPLEMENTS"]:
                for direction in ["parent", "child"]:
                    if direction == "parent":
                        cypher = (
                            f"MATCH (child)-[:{rel}]->(parent) "
                            f"WHERE child.name = '{esc}' OR child.qualifiedName = '{esc}' "
                            f"RETURN child.qualifiedName AS child, parent.qualifiedName AS parent, '{rel}' AS rel"
                        )
                    else:
                        cypher = (
                            f"MATCH (child)-[:{rel}]->(parent) "
                            f"WHERE parent.name = '{esc}' OR parent.qualifiedName = '{esc}' "
                            f"RETURN child.qualifiedName AS child, parent.qualifiedName AS parent, '{rel}' AS rel"
                        )
                    try:
                        rows = backend.execute(cypher)
                        for r in rows:
                            lines.append(f"{r['child']} --{r['rel']}--> {r['parent']}")
                    except Exception:
                        pass
            return "\n".join(lines) if lines else f"No hierarchy found for '{class_name}'."

        # ------------------------------------------------------------------ #
        # get_control_flow
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_control_flow(method_name: str) -> str:
            """Get the control-flow graph (CFG) edges for a method or function."""
            esc = _cypher_escape(method_name)
            cypher = (
                f"MATCH (m)-[:CFG_NEXT*1..1]->(s:Statement) "
                f"WHERE m.name = '{esc}' OR m.qualifiedName = '{esc}' "
                f"WITH s "
                f"MATCH (s)-[r:CFG_NEXT]->(t:Statement) "
                f"RETURN s.code AS from_stmt, s.lineNumber AS from_line, "
                f"t.code AS to_stmt, t.lineNumber AS to_line, "
                f"r.backEdge AS backEdge LIMIT 100"
            )
            try:
                rows = backend.execute(cypher)
                return _format_rows(rows) if rows else f"No CFG edges found for '{method_name}'."
            except Exception as e:
                return f"Error: {e}"

        # ------------------------------------------------------------------ #
        # get_data_flow
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_data_flow(method_name: str) -> str:
            """Get data-flow edges for a method or function, showing how variables propagate."""
            esc = _cypher_escape(method_name)
            cypher = (
                f"MATCH (m)-[:CFG_NEXT*1..1]->(s:Statement) "
                f"WHERE m.name = '{esc}' OR m.qualifiedName = '{esc}' "
                f"WITH s "
                f"MATCH (s)-[r:DATA_FLOW]->(t:Statement) "
                f"RETURN s.code AS from_stmt, s.lineNumber AS from_line, "
                f"r.variable AS variable, "
                f"t.code AS to_stmt, t.lineNumber AS to_line LIMIT 100"
            )
            try:
                rows = backend.execute(cypher)
                return _format_rows(rows) if rows else f"No data-flow edges found for '{method_name}'."
            except Exception as e:
                return f"Error: {e}"

        # ------------------------------------------------------------------ #
        # get_architecture_overview
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_architecture_overview() -> str:
            """Get a high-level architecture overview: all packages with their class/interface
            counts and inter-package dependencies. Starting point for reverse engineering."""
            lines = ["=== ARCHITECTURE OVERVIEW ===\n"]

            # Packages and contents
            try:
                rows = backend.execute(
                    "MATCH (p:Package)-[:CONTAINS]->(c) "
                    "RETURN p.qualifiedName AS package, label(c) AS type, count(c) AS count "
                    "ORDER BY package"
                )
                if rows:
                    lines.append("── Packages & Contents ──")
                    pkg_map: dict[str, list[str]] = {}
                    for r in rows:
                        pkg = r.get("package", "unknown")
                        pkg_map.setdefault(pkg, []).append(f"{r.get('type', '?')}: {r.get('count', 0)}")
                    for pkg, contents in sorted(pkg_map.items()):
                        lines.append(f"  {pkg}")
                        for c in contents:
                            lines.append(f"    {c}")
            except Exception as e:
                lines.append(f"  (package query error: {e})")

            # Inter-package dependencies
            try:
                rows = backend.execute(
                    "MATCH (p1:Package)-[:CONTAINS]->(c1)-[:CALLS|EXTENDS|IMPLEMENTS|OF_TYPE]->(c2)<-[:CONTAINS]-(p2:Package) "
                    "WHERE p1.qualifiedName <> p2.qualifiedName "
                    "RETURN p1.qualifiedName AS from_pkg, p2.qualifiedName AS to_pkg, count(*) AS weight "
                    "ORDER BY weight DESC LIMIT 50"
                )
                if rows:
                    lines.append("\n── Inter-Package Dependencies ──")
                    for r in rows:
                        lines.append(f"  {r.get('from_pkg', '')} --> {r.get('to_pkg', '')}  (weight: {r.get('weight', 0)})")
            except Exception as e:
                lines.append(f"  (inter-package query error: {e})")

            # File count
            try:
                rows = backend.execute("MATCH (f:File) RETURN count(f) AS count")
                if rows:
                    lines.append(f"\n── Source Files: {rows[0].get('count', 0)} ──")
            except Exception:
                pass

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_package_contents
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_package_contents(package_name: str) -> str:
            """Get all classes, interfaces, enums, and other types within a specific package."""
            esc = _cypher_escape(package_name)
            cypher = (
                f"MATCH (p:Package)-[:CONTAINS]->(child) "
                f"WHERE p.name = '{esc}' OR p.qualifiedName = '{esc}' "
                f"RETURN child.name AS name, child.qualifiedName AS qualifiedName, "
                f"label(child) AS type, child.visibility AS visibility, "
                f"child.isAbstract AS isAbstract, child.path AS path "
                f"ORDER BY type, name"
            )
            try:
                rows = backend.execute(cypher)
                return _format_rows(rows) if rows else f"No contents found for package '{package_name}'."
            except Exception as e:
                return f"Error: {e}"

        # ------------------------------------------------------------------ #
        # get_class_details
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_class_details(class_name: str) -> str:
            """Get comprehensive details of a class: fields, methods, constructors,
            annotations, inheritance, and source file location."""
            esc = _cypher_escape(class_name)
            lines = [f"=== CLASS DETAILS: {class_name} ===\n"]

            # Basic info
            for label in ["Class", "Interface", "Enum", "Record", "DataClass", "SealedClass"]:
                try:
                    rows = backend.execute(
                        f"MATCH (c:{label}) WHERE c.name = '{esc}' OR c.qualifiedName = '{esc}' "
                        f"RETURN c.name AS name, c.qualifiedName AS qualifiedName, "
                        f"c.visibility AS visibility, c.isAbstract AS isAbstract, "
                        f"c.isFinal AS isFinal, c.path AS path, c.lineNumber AS line"
                    )
                    if rows:
                        lines.append(f"Type: {label}")
                        for k, v in rows[0].items():
                            if v is not None:
                                lines.append(f"  {k}: {v}")
                        break
                except Exception:
                    pass

            # Methods
            try:
                rows = backend.execute(
                    f"MATCH (c)-[:CONTAINS]->(m:Method) "
                    f"WHERE c.name = '{esc}' OR c.qualifiedName = '{esc}' "
                    f"RETURN m.name AS name, m.visibility AS visibility, "
                    f"m.returnType AS returnType, m.isStatic AS isStatic, m.lineNumber AS line "
                    f"ORDER BY m.lineNumber"
                )
                if rows:
                    lines.append(f"\n── Methods ({len(rows)}) ──")
                    for r in rows:
                        vis = r.get("visibility", "")
                        static = " static" if r.get("isStatic") else ""
                        ret = r.get("returnType", "void")
                        lines.append(f"  {vis}{static} {ret} {r.get('name', '')}  (line {r.get('line', '')})")
            except Exception:
                pass

            # Fields
            try:
                rows = backend.execute(
                    f"MATCH (c)-[:CONTAINS]->(f:Field) "
                    f"WHERE c.name = '{esc}' OR c.qualifiedName = '{esc}' "
                    f"RETURN f.name AS name, f.visibility AS visibility, "
                    f"f.isStatic AS isStatic, f.isFinal AS isFinal, f.type AS type "
                    f"ORDER BY f.name"
                )
                if rows:
                    lines.append(f"\n── Fields ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  {r.get('visibility', '')} {r.get('type', '')} {r.get('name', '')}")
            except Exception:
                pass

            # Constructors
            try:
                rows = backend.execute(
                    f"MATCH (c)-[:CONTAINS]->(con:Constructor) "
                    f"WHERE c.name = '{esc}' OR c.qualifiedName = '{esc}' "
                    f"RETURN con.name AS name, con.visibility AS visibility, con.lineNumber AS line"
                )
                if rows:
                    lines.append(f"\n── Constructors ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  {r.get('visibility', '')} {r.get('name', '')}  (line {r.get('line', '')})")
            except Exception:
                pass

            # Annotations
            try:
                rows = backend.execute(
                    f"MATCH (c)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                    f"WHERE c.name = '{esc}' OR c.qualifiedName = '{esc}' "
                    f"RETURN a.name AS annotation"
                )
                if rows:
                    lines.append("\n── Annotations ──")
                    for r in rows:
                        lines.append(f"  @{r.get('annotation', '')}")
            except Exception:
                pass

            # Source file
            try:
                rows = backend.execute(
                    f"MATCH (c)-[:SOURCE_FILE]->(f:File) "
                    f"WHERE c.name = '{esc}' OR c.qualifiedName = '{esc}' "
                    f"RETURN f.path AS path"
                )
                if rows:
                    lines.append("\n── Source File ──")
                    lines.append(f"  {rows[0].get('path', '')}")
            except Exception:
                pass

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_entry_points
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_entry_points() -> str:
            """Discover application entry points: annotated controllers/routers, main methods, exported modules, CLI commands."""
            lines = ["=== ENTRY POINTS ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (n) WHERE n.normKind = 'Entrypoint' "
                    "RETURN DISTINCT n.qualifiedName AS target, label(n) AS type, n.language AS language "
                    "ORDER BY language, target LIMIT 50"
                )
                if rows:
                    lines.append("── Normalized Entrypoints ──")
                    for r in rows:
                        lines.append(f"  [{r.get('language', '?')}] {r.get('type', '')}: {r.get('target', '')}")
            except Exception:
                pass

            # 1. Dynamic annotation-based discovery (works across Java/Kotlin/Python/JS)
            try:
                rows = backend.execute(
                    "MATCH (c)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                    "WHERE a.name =~ '(?i).*(Controller|Application|Servlet|WebSocket|Endpoint|Router|Blueprint|App|Main|Command|CLI).*' "
                    "RETURN DISTINCT c.qualifiedName AS target, a.name AS annotation, "
                    "label(c) AS type ORDER BY annotation, target"
                )
                if rows:
                    lines.append("── Annotated Entry Points ──")
                    for r in rows:
                        lines.append(f"  @{r.get('annotation', '')} {r.get('type', '')}: {r.get('target', '')}")
            except Exception as e:
                lines.append(f"  (annotation search error: {e})")

            # 2. main() methods and functions (Java, Kotlin, Python)
            try:
                rows = backend.execute(
                    "MATCH (m) WHERE (label(m) = 'Method' OR label(m) = 'Function') AND m.name = 'main' "
                    "RETURN m.qualifiedName AS entry, label(m) AS type, "
                    "m.visibility AS visibility ORDER BY m.qualifiedName"
                )
                if rows:
                    lines.append("\n── main() Entry Points ──")
                    for r in rows:
                        lines.append(f"  {r.get('type', '')}: {r.get('entry', '')}")
            except Exception as e:
                lines.append(f"  (main search error: {e})")

            # 3. Module exports (JS/TS)
            try:
                rows = backend.execute(
                    "MATCH (m)-[:EXPORTS]->(e) "
                    "RETURN m.qualifiedName AS module, e.name AS export, "
                    "label(e) AS type, e.language AS language LIMIT 30"
                )
                if rows:
                    lines.append("\n── Module Exports ──")
                    for r in rows:
                        lines.append(f"  [{r.get('language', '?')}] {r.get('module', '')} exports {r.get('type', '')}: {r.get('export', '')}")
            except Exception:
                pass

            # 4. Public static methods with no callers (potential entry points)
            try:
                rows = backend.execute(
                    "MATCH (m:Method) "
                    "WHERE m.visibility = 'public' AND m.isStatic = true "
                    "AND NOT EXISTS { MATCH ()-[:CALLS]->(m) } "
                    "RETURN m.qualifiedName AS method, m.name AS name "
                    "ORDER BY m.qualifiedName LIMIT 30"
                )
                if rows:
                    lines.append("\n── Public Static Methods with No Callers ──")
                    for r in rows:
                        lines.append(f"  {r.get('method', '')}")
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No entry points detected."

        # ------------------------------------------------------------------ #
        # get_api_endpoints
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_api_endpoints() -> str:
            """Discover REST/HTTP API endpoints across frameworks (Spring, Flask, FastAPI, Express, etc.)."""
            lines = ["=== API ENDPOINTS ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (n) WHERE n.normKind = 'Entrypoint' "
                    "AND n.name =~ '(?i).*(route|endpoint|handler|controller|api|get_|post_|put_|delete_).*' "
                    "RETURN DISTINCT n.qualifiedName AS name, label(n) AS type, n.language AS language "
                    "ORDER BY language, name LIMIT 30"
                )
                if rows:
                    lines.append("── Normalized Endpoint Candidates ──")
                    for r in rows:
                        lines.append(f"  [{r.get('language', '?')}] {r.get('type', '')}: {r.get('name', '')}")
            except Exception:
                pass

            # 1. Annotation-based HTTP endpoints (Java/Kotlin Spring, JAX-RS, etc.)
            try:
                rows = backend.execute(
                    "MATCH (m)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                    "WHERE a.name =~ '(?i).*(Mapping|Route|Endpoint|Api|GET|POST|PUT|DELETE|PATCH|Path|Produces|Consumes).*' "
                    "RETURN m.qualifiedName AS method, m.name AS name, "
                    "a.name AS annotation, m.lineNumber AS line, label(m) AS type "
                    "ORDER BY annotation, method"
                )
                if rows:
                    lines.append("── HTTP-Annotated Endpoints ──")
                    for r in rows:
                        lines.append(f"  @{r.get('annotation', '')} {r.get('method', '')}  (line {r.get('line', '')})")
            except Exception as e:
                lines.append(f"  (annotation search error: {e})")

            # 2. Controller/Router classes
            try:
                rows = backend.execute(
                    "MATCH (c)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                    "WHERE a.name =~ '(?i).*(Controller|RestController|Resource|Router|Blueprint|Api).*' "
                    "RETURN c.qualifiedName AS controller, a.name AS annotation, "
                    "c.path AS path, label(c) AS type"
                )
                if rows:
                    lines.append("\n── Controller/Router Classes ──")
                    for r in rows:
                        lines.append(f"  @{r.get('annotation', '')} {r.get('controller', '')}  ({r.get('path', '')})")
            except Exception:
                pass

            # 3. Route-related functions by naming convention (Flask/FastAPI/Express)
            try:
                rows = backend.execute(
                    "MATCH (f) WHERE (label(f) = 'Function' OR label(f) = 'ArrowFunction' OR label(f) = 'Method') "
                    "AND f.name =~ '(?i).*(route|endpoint|handler|api_|get_|post_|put_|delete_).*' "
                    "RETURN f.qualifiedName AS name, label(f) AS type, "
                    "f.lineNumber AS line LIMIT 30"
                )
                if rows:
                    lines.append("\n── Route/Handler Functions (by name) ──")
                    for r in rows:
                        lines.append(f"  {r.get('type', '')}: {r.get('name', '')}  (line {r.get('line', '')})")
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No API endpoints detected."

        # ------------------------------------------------------------------ #
        # get_route_map
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_route_map() -> str:
            """Summarize frontend route/screen candidates and their owning modules."""
            lines = ["=== ROUTE MAP ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (n) "
                    "WHERE label(n) IN ['Component', 'Function', 'ArrowFunction', 'Module'] "
                    "AND ("
                    "n.name =~ '(?i).*(route|router|page|screen|layout|view).*' "
                    "OR coalesce(n.path, '') =~ '(?i).*(pages|screens|views|routes|app/|src/app).*'"
                    ") "
                    "OPTIONAL MATCH (owner)-[:CONTAINS]->(n) "
                    "RETURN DISTINCT n.name AS name, label(n) AS type, n.path AS path, "
                    "owner.qualifiedName AS owner "
                    "ORDER BY path, name LIMIT 60"
                )
                if rows:
                    lines.append("── Route / Screen Candidates ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (route scan error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (m:Module)-[:EXPORTS]->(n) "
                    "WHERE n.name =~ '(?i).*(route|router|page|screen|layout|view).*' "
                    "RETURN m.qualifiedName AS module, n.name AS exported, label(n) AS type "
                    "ORDER BY module, exported LIMIT 40"
                )
                if rows:
                    lines.append("\n── Exported Route Entries ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            try:
                rows = backend.execute(
                    "MATCH (c:Component) "
                    "WHERE coalesce(c.path, '') =~ '(?i).*(components|pages|screens|views|routes|app/|src/app).*' "
                    "RETURN DISTINCT c.qualifiedName AS component, c.path AS path "
                    "ORDER BY path, component LIMIT 60"
                )
                if rows:
                    lines.append("\n── Component Surface ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No route-like frontend structures detected."

        # ------------------------------------------------------------------ #
        # get_component_boundary_map
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_component_boundary_map() -> str:
            """Summarize component/module ownership and render relationships for frontend analysis."""
            lines = ["=== COMPONENT BOUNDARIES ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (owner)-[:CONTAINS]->(c:Component) "
                    "RETURN owner.qualifiedName AS owner_name, c.qualifiedName AS component_name, c.path AS path "
                    "LIMIT 80"
                )
                if rows:
                    rows = sorted(rows, key=lambda r: ((r.get("owner_name") or ""), (r.get("component_name") or "")))
                    lines.append("── Owned Components ──")
                    lines.append(_format_rows([
                        {"owner": r.get("owner_name"), "component": r.get("component_name"), "path": r.get("path")}
                        for r in rows
                    ]))
            except Exception as e:
                lines.append(f"  (component ownership error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (a)-[r:RENDERS]->(b) "
                    "RETURN a.qualifiedName AS parent, b.qualifiedName AS child, r.lineNumber AS line "
                    "ORDER BY parent, child LIMIT 80"
                )
                if rows:
                    lines.append("\n── Render Relationships ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            try:
                rows = backend.execute(
                    "MATCH (a)-[:PROP_DEPENDENCY]->(b) "
                    "RETURN a.qualifiedName AS owner, b.qualifiedName AS dependency "
                    "ORDER BY owner, dependency LIMIT 80"
                )
                if rows:
                    lines.append("\n── Prop Dependencies ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No component/render boundaries detected."

        # ------------------------------------------------------------------ #
        # get_state_management_summary
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_state_management_summary() -> str:
            """Summarize hooks, stores, contexts, and state-like dependencies."""
            lines = ["=== STATE MANAGEMENT ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (h:Hook) "
                    "RETURN h.qualifiedName AS hook, h.path AS path "
                    "ORDER BY hook LIMIT 50"
                )
                if rows:
                    lines.append("── Hooks ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (hook scan error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (consumer)-[r:USES_HOOK]->(h:Hook) "
                    "RETURN consumer.qualifiedName AS consumer, h.qualifiedName AS hook, r.lineNumber AS line "
                    "ORDER BY consumer, hook LIMIT 80"
                )
                if rows:
                    lines.append("\n── Hook Consumers ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            try:
                rows = backend.execute(
                    "MATCH (n) "
                    "WHERE label(n) IN ['Component', 'Module', 'Function'] "
                    "AND n.name =~ '(?i).*(store|context|provider|state|query|cache|reducer).*' "
                    "RETURN DISTINCT label(n) AS type, n.qualifiedName AS name, n.path AS path "
                    "ORDER BY type, name LIMIT 60"
                )
                if rows:
                    lines.append("\n── Store / Context Candidates ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            try:
                rows = backend.execute(
                    "MATCH (consumer)-[:CALLS|IMPORTS]->(state) "
                    "WHERE state.name =~ '(?i).*(store|context|provider|state|query|cache|reducer).*' "
                    "RETURN DISTINCT consumer.qualifiedName AS consumer, label(consumer) AS consumer_type, "
                    "state.qualifiedName AS state_target, label(state) AS state_type "
                    "ORDER BY consumer, state_target LIMIT 80"
                )
                if rows:
                    lines.append("\n── State Consumers ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No state-management structures detected."

        # ------------------------------------------------------------------ #
        # get_api_client_summary
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_api_client_summary() -> str:
            """Summarize frontend API client modules, fetch wrappers, and gateway-like integrations."""
            lines = ["=== API CLIENT SUMMARY ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (n) "
                    "WHERE label(n) IN ['Module', 'Function', 'Class', 'Component'] "
                    "AND n.name =~ '(?i).*(client|api|fetch|axios|gateway|service|query).*' "
                    "RETURN DISTINCT label(n) AS type, n.qualifiedName AS name, n.path AS path "
                    "ORDER BY type, name LIMIT 60"
                )
                if rows:
                    lines.append("── API Client Candidates ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (api client scan error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (m:Module)-[:IMPORTS]->(dep) "
                    "WHERE dep.name =~ '(?i).*(axios|fetch|graphql|apollo|swr|react-query).*' "
                    "RETURN m.qualifiedName AS module, dep.name AS dependency "
                    "ORDER BY module, dependency LIMIT 40"
                )
                if rows:
                    lines.append("\n── Data Fetching Dependencies ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            try:
                rows = backend.execute(
                    "MATCH (caller)-[r:CALLS]->(target) "
                    "WHERE target.name =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request).*' "
                    "OR coalesce(target.qualifiedName, '') =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request).*' "
                    "RETURN DISTINCT caller.qualifiedName AS caller, label(caller) AS caller_type, "
                    "target.qualifiedName AS target, r.lineNumber AS line "
                    "ORDER BY caller, target LIMIT 80"
                )
                if rows:
                    lines.append("\n── Call Sites Into API Clients ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No API client structures detected."

        # ------------------------------------------------------------------ #
        # get_component_tree
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_component_tree() -> str:
            """Summarize component roots and render hierarchy for React-style frontends."""
            lines = ["=== COMPONENT TREE ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (c:Component) "
                    "WHERE NOT EXISTS { MATCH ()-[:RENDERS]->(c) } "
                    "RETURN DISTINCT c.qualifiedName AS root, c.path AS path "
                    "ORDER BY path, root LIMIT 40"
                )
                if rows:
                    lines.append("── Root Components ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (root component scan error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (a)-[r:RENDERS]->(b:Component) "
                    "RETURN a.qualifiedName AS parent, b.qualifiedName AS child, r.lineNumber AS line "
                    "ORDER BY parent, child LIMIT 120"
                )
                if rows:
                    lines.append("\n── Render Tree Edges ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No component tree detected."

        # ------------------------------------------------------------------ #
        # get_hook_usage_graph
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_hook_usage_graph() -> str:
            """Summarize which components and hooks depend on which hooks and async client calls."""
            lines = ["=== HOOK USAGE GRAPH ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (consumer)-[r:USES_HOOK]->(h:Hook) "
                    "RETURN consumer.qualifiedName AS consumer, label(consumer) AS consumer_type, "
                    "h.qualifiedName AS hook, r.lineNumber AS line "
                    "ORDER BY consumer, hook LIMIT 120"
                )
                if rows:
                    lines.append("── Hook Dependencies ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (hook dependency error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (h:Hook)-[r:CALLS]->(target) "
                    "WHERE target.name =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request).*' "
                    "OR coalesce(target.qualifiedName, '') =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request).*' "
                    "RETURN h.qualifiedName AS hook, target.qualifiedName AS target, r.lineNumber AS line "
                    "ORDER BY hook, target LIMIT 80"
                )
                if rows:
                    lines.append("\n── Hook Calls Into API Clients ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No hook usage graph detected."

        # ------------------------------------------------------------------ #
        # get_route_component_map
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_route_component_map() -> str:
            """Map route/page/layout entries to owning modules and rendered components."""
            lines = ["=== ROUTE COMPONENT MAP ===\n"]
            found_rows = False

            try:
                rows = backend.execute(
                    "MATCH (entry) "
                    "WHERE label(entry) IN ['Component', 'Function', 'ArrowFunction', 'Module'] "
                    "AND ("
                    "entry.name =~ '(?i).*(route|router|page|screen|layout|view).*' "
                    "OR coalesce(entry.path, '') =~ '(?i).*(pages|screens|views|routes|app/|src/app).*'"
                    ") "
                    "OPTIONAL MATCH (owner)-[:CONTAINS]->(entry) "
                    "RETURN DISTINCT entry.qualifiedName AS entry, label(entry) AS entry_type, entry.path AS path, "
                    "owner.qualifiedName AS owner "
                    "ORDER BY path, entry LIMIT 80"
                )
                if rows:
                    found_rows = True
                    lines.append("── Route / Page Entries ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (route entry error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (entry)-[r:RENDERS]->(child:Component) "
                    "WHERE (entry.name =~ '(?i).*(route|router|page|screen|layout|view).*' "
                    "OR coalesce(entry.path, '') =~ '(?i).*(pages|screens|views|routes|app/|src/app).*') "
                    "RETURN entry.qualifiedName AS entry, child.qualifiedName AS child_component, r.lineNumber AS line "
                    "ORDER BY entry, child_component LIMIT 120"
                )
                if rows:
                    found_rows = True
                    lines.append("\n── Route To Component Edges ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            if not found_rows:
                try:
                    rows = backend.execute(
                        "MATCH (entry) "
                        "WHERE label(entry) IN ['Component', 'Function', 'ArrowFunction', 'Module'] "
                        "AND ("
                        "entry.name =~ '(?i).*(app|root|shell|main).*' "
                        "OR coalesce(entry.path, '') =~ '(?i).*(components|src/|admin-frontend|frontend).*'"
                        ") "
                        "OPTIONAL MATCH (owner)-[:CONTAINS]->(entry) "
                        "RETURN DISTINCT entry.qualifiedName AS entry, label(entry) AS entry_type, entry.path AS path, "
                        "owner.qualifiedName AS owner "
                        "ORDER BY entry_type DESC, path, entry LIMIT 40"
                    )
                    if rows:
                        found_rows = True
                        lines.append("\n── UI Entry Surfaces (SPA Fallback) ──")
                        lines.append(_format_rows(rows))
                except Exception:
                    pass

                try:
                    rows = backend.execute(
                        "MATCH (entry)-[r:RENDERS]->(child:Component) "
                        "WHERE entry.name =~ '(?i).*(app|root|shell|main).*' "
                        "OR coalesce(entry.path, '') =~ '(?i).*(components|src/|admin-frontend|frontend).*' "
                        "RETURN entry.qualifiedName AS entry, child.qualifiedName AS child_component, r.lineNumber AS line "
                        "ORDER BY entry, child_component LIMIT 120"
                    )
                    if rows:
                        found_rows = True
                        lines.append("\n── UI Entry To Component Edges (SPA Fallback) ──")
                        lines.append(_format_rows(rows))
                except Exception:
                    pass

            return "\n".join(lines) if len(lines) > 1 else "No route-to-component map detected."

        # ------------------------------------------------------------------ #
        # get_state_ownership_map
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_state_ownership_map() -> str:
            """Summarize state owners and their consuming components/hooks."""
            lines = ["=== STATE OWNERSHIP MAP ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (state) "
                    "WHERE label(state) IN ['Hook', 'Component', 'Module', 'Function'] "
                    "AND (label(state) = 'Hook' "
                    "OR state.name =~ '(?i).*(store|context|provider|state|query|cache|reducer).*') "
                    "RETURN DISTINCT state.qualifiedName AS state_owner, label(state) AS state_type, state.path AS path "
                    "ORDER BY state_type, state_owner LIMIT 80"
                )
                if rows:
                    lines.append("── State Owners ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (state owner error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (consumer)-[:USES_HOOK|CALLS|IMPORTS]->(state) "
                    "WHERE label(state) = 'Hook' "
                    "OR state.name =~ '(?i).*(store|context|provider|state|query|cache|reducer).*' "
                    "RETURN DISTINCT consumer.qualifiedName AS consumer, label(consumer) AS consumer_type, "
                    "state.qualifiedName AS state_owner, label(state) AS state_type "
                    "ORDER BY consumer, state_owner LIMIT 120"
                )
                if rows:
                    lines.append("\n── Consumers ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No state ownership map detected."

        # ------------------------------------------------------------------ #
        # get_ui_to_api_call_map
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_ui_to_api_call_map() -> str:
            """Map UI components/routes/hooks to API client calls and likely backend endpoints."""
            lines = ["=== UI TO API CALL MAP ===\n"]
            found_rows = False

            try:
                ui_client_rows = backend.execute(
                    "MATCH (ui)-[r:CALLS]->(target) "
                    "WHERE label(ui) IN ['Component', 'Hook', 'Function', 'ArrowFunction', 'AsyncFunction'] "
                    "AND (label(ui) = 'Component' "
                    "OR ui.name =~ '(?i).*(route|router|page|screen|layout|view).*' "
                    "OR coalesce(ui.path, '') =~ '(?i).*(components|pages|screens|views|routes|app/|src/app).*') "
                    "AND (target.name =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request|get|post|put|delete).*' "
                    "OR coalesce(target.qualifiedName, '') =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request|get|post|put|delete).*') "
                    "RETURN DISTINCT ui.qualifiedName AS ui, label(ui) AS ui_type, ui.path AS ui_path, "
                    "target.qualifiedName AS client, label(target) AS client_type, target.path AS client_path, r.lineNumber AS line "
                    "ORDER BY ui, client LIMIT 160"
                )
            except Exception as e:
                return f"UI/API map error: {e}"

            endpoint_rows: list[dict[str, Any]] = []
            try:
                endpoint_rows = backend.execute(
                    "MATCH (n) WHERE n.normKind = 'Entrypoint' "
                    "AND n.name =~ '(?i).*(route|endpoint|handler|controller|api|get_|post_|put_|delete_).*' "
                    "RETURN DISTINCT n.qualifiedName AS endpoint, n.name AS endpoint_name, label(n) AS endpoint_type "
                    "ORDER BY endpoint LIMIT 80"
                )
            except Exception:
                endpoint_rows = []

            if ui_client_rows:
                found_rows = True
                lines.append("── UI To Client Calls ──")
                mapped_rows: list[dict[str, Any]] = []
                for row in ui_client_rows:
                    client_name = str(row.get("client", "") or "")
                    client_tokens = _name_tokens(client_name)
                    matched: list[str] = []
                    for endpoint in endpoint_rows:
                        endpoint_name = str(endpoint.get("endpoint", "") or endpoint.get("endpoint_name", "") or "")
                        if client_tokens and client_tokens.intersection(_name_tokens(endpoint_name)):
                            matched.append(endpoint_name)
                    mapped_rows.append(
                        {
                            "ui": row.get("ui"),
                            "ui_type": row.get("ui_type"),
                            "client": client_name,
                            "line": row.get("line"),
                            "probable_endpoints": matched[:3] or ["no direct endpoint match"],
                        }
                    )
                lines.append(_format_rows(mapped_rows))
            else:
                try:
                    import_rows = backend.execute(
                        "MATCH (ui:Module)-[r:IMPORTS]->(target:Module) "
                        "WHERE (coalesce(ui.path, '') =~ '(?i).*(components|pages|screens|views|routes|app/|src/app|admin-frontend|frontend).*' "
                        "OR ui.qualifiedName =~ '(?i).*(components|pages|screens|views|routes|app|admin-frontend|frontend).*') "
                        "AND (target.name =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request).*' "
                        "OR coalesce(target.qualifiedName, '') =~ '(?i).*(client|api|fetch|axios|gateway|service|query|request).*') "
                        "RETURN DISTINCT ui.qualifiedName AS ui, 'Module' AS ui_type, ui.path AS ui_path, "
                        "target.qualifiedName AS client, 'Module' AS client_type, target.path AS client_path, r.localName AS line "
                        "ORDER BY ui, client LIMIT 160"
                    )
                except Exception:
                    import_rows = []

                if import_rows:
                    found_rows = True
                    lines.append("── UI Module Imports To API Modules (Fallback) ──")
                    mapped_rows = []
                    for row in import_rows:
                        client_name = str(row.get("client", "") or "")
                        client_tokens = _name_tokens(client_name)
                        matched = []
                        for endpoint in endpoint_rows:
                            endpoint_name = str(endpoint.get("endpoint", "") or endpoint.get("endpoint_name", "") or "")
                            if client_tokens and client_tokens.intersection(_name_tokens(endpoint_name)):
                                matched.append(endpoint_name)
                        mapped_rows.append(
                            {
                                "ui": row.get("ui"),
                                "ui_type": row.get("ui_type"),
                                "client": client_name,
                                "line": row.get("line"),
                                "probable_endpoints": matched[:3] or ["no direct endpoint match"],
                            }
                        )
                    lines.append(_format_rows(mapped_rows))

            if endpoint_rows:
                lines.append("\n── Backend Endpoint Candidates ──")
                lines.append(_format_rows(endpoint_rows))

            return "\n".join(lines) if found_rows or endpoint_rows else "No UI-to-API call map detected."

        # ------------------------------------------------------------------ #
        # get_frontend_architecture_summary
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_frontend_architecture_summary() -> str:
            """Return a combined frontend architecture summary using the dedicated frontend tools."""
            parts = [
                get_route_component_map(),
                get_component_tree(),
                get_state_ownership_map(),
                get_ui_to_api_call_map(),
            ]
            return "\n\n".join(part for part in parts if part and "No " not in part[:30]) or "No frontend architecture summary detected."

        # ------------------------------------------------------------------ #
        # get_public_api_surface
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_public_api_surface() -> str:
            """Summarize exported/public library API surface."""
            lines = ["=== PUBLIC API SURFACE ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (m:Module)-[:EXPORTS]->(n) "
                    "RETURN m.qualifiedName AS module, n.name AS export, label(n) AS type, n.path AS path "
                    "ORDER BY module, export LIMIT 80"
                )
                if rows:
                    lines.append("── Exported Symbols ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (exports scan error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (n:Method) "
                    "WHERE n.visibility = 'public' "
                    "RETURN n.qualifiedName AS symbol, 'Method' AS type, n.path AS path "
                    "ORDER BY symbol LIMIT 80"
                )
                if rows:
                    lines.append("\n── Public Methods ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No public API surface detected."

        # ------------------------------------------------------------------ #
        # get_extension_points
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_extension_points() -> str:
            """Summarize library extension seams such as interfaces, abstract classes, hooks, and plugins."""
            lines = ["=== EXTENSION POINTS ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (n) "
                    "WHERE label(n) IN ['Interface', 'AbstractClass', 'SealedClass', 'Hook', 'Function'] "
                    "OR n.name =~ '(?i).*(plugin|extension|hook|adapter|strategy|callback|listener|provider).*' "
                    "RETURN DISTINCT label(n) AS type, n.qualifiedName AS name, n.path AS path "
                    "ORDER BY type, name LIMIT 80"
                )
                if rows:
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (extension-point scan error: {e})")

            return "\n".join(lines) if len(lines) > 1 else "No extension points detected."

        # ------------------------------------------------------------------ #
        # get_module_dependency_map
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_module_dependency_map() -> str:
            """Summarize module/package dependencies using imports, calls, and containment."""
            lines = ["=== MODULE DEPENDENCY MAP ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (a)-[:IMPORTS|CALLS]->(b) "
                    "WHERE a.path IS NOT NULL AND b.path IS NOT NULL AND a.path <> b.path "
                    "RETURN a.path AS from_path, b.path AS to_path, count(*) AS weight "
                    "ORDER BY weight DESC, from_path, to_path LIMIT 80"
                )
                if rows:
                    lines.append("── File / Module Dependencies ──")
                    lines.append(_format_rows(rows))
            except Exception as e:
                lines.append(f"  (dependency-map error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (m:Module)-[:IMPORTS]->(n) "
                    "RETURN m.qualifiedName AS module, n.name AS dependency, label(n) AS type "
                    "ORDER BY module, dependency LIMIT 80"
                )
                if rows:
                    lines.append("\n── Module Imports ──")
                    lines.append(_format_rows(rows))
            except Exception:
                pass

            return "\n".join(lines) if len(lines) > 1 else "No module dependency data detected."

        # ------------------------------------------------------------------ #
        # get_dependency_graph
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_dependency_graph(node_name: str, direction: str = "both", depth: int = 2) -> str:
            """Get the transitive dependency graph for a class/method.

            Args:
                node_name: Name or qualified name of the class/method.
                direction: 'outgoing', 'incoming', or 'both'. Default 'both'.
                depth: How many levels deep (1-5). Default 2.
            """
            depth = max(1, min(depth, 5))
            esc = _cypher_escape(node_name)
            lines = [f"=== DEPENDENCY GRAPH: {node_name} (depth={depth}, direction={direction}) ===\n"]

            if direction in ("outgoing", "both"):
                try:
                    rows = backend.execute(
                        f"MATCH path = (source)-[:CALLS|EXTENDS|IMPLEMENTS|OF_TYPE*1..{depth}]->(target) "
                        f"WHERE source.name = '{esc}' OR source.qualifiedName = '{esc}' "
                        f"RETURN DISTINCT target.qualifiedName AS dependency, "
                        f"label(target) AS type, length(path) AS distance "
                        f"ORDER BY distance, dependency LIMIT 100"
                    )
                    if rows:
                        lines.append("── Outgoing Dependencies ──")
                        for r in rows:
                            indent = "  " * r.get("distance", 1)
                            lines.append(f"{indent}{r.get('type', '')}: {r.get('dependency', '')}")
                except Exception as e:
                    lines.append(f"  (outgoing query error: {e})")

            if direction in ("incoming", "both"):
                try:
                    rows = backend.execute(
                        f"MATCH path = (source)-[:CALLS|EXTENDS|IMPLEMENTS|OF_TYPE*1..{depth}]->(target) "
                        f"WHERE target.name = '{esc}' OR target.qualifiedName = '{esc}' "
                        f"RETURN DISTINCT source.qualifiedName AS dependent, "
                        f"label(source) AS type, length(path) AS distance "
                        f"ORDER BY distance, dependent LIMIT 100"
                    )
                    if rows:
                        lines.append("\n── Incoming Dependencies ──")
                        for r in rows:
                            indent = "  " * r.get("distance", 1)
                            lines.append(f"{indent}{r.get('type', '')}: {r.get('dependent', '')}")
                except Exception as e:
                    lines.append(f"  (incoming query error: {e})")

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # find_path_between
        # ------------------------------------------------------------------ #
        @reg.tool()
        def find_path_between(source_name: str, target_name: str, max_depth: int = 6) -> str:
            """Find the shortest path between two nodes in the code graph."""
            max_depth = max(1, min(max_depth, 10))
            src = _cypher_escape(source_name)
            tgt = _cypher_escape(target_name)
            cypher = (
                f"MATCH path = (a)-[*1..{max_depth}]->(b) "
                f"WHERE (a.name = '{src}' OR a.qualifiedName = '{src}') "
                f"  AND (b.name = '{tgt}' OR b.qualifiedName = '{tgt}') "
                f"RETURN nodes(path) AS path_nodes, "
                f"length(path) AS pathLength "
                f"ORDER BY pathLength LIMIT 1"
            )
            try:
                rows = backend.execute(cypher)
                if rows:
                    lines = []
                    for r in rows:
                        path_nodes = r.get("path_nodes", [])
                        names = [n.get("qualifiedName", "?") if isinstance(n, dict) else str(n) for n in path_nodes]
                        path_str = " --> ".join(names)
                        lines.append(f"Path (length {r.get('pathLength', '')}): {path_str}")
                    return "\n".join(lines)
                return f"No path found between '{source_name}' and '{target_name}' within depth {max_depth}."
            except Exception as e:
                return f"Path query error: {e}"

        # ------------------------------------------------------------------ #
        # get_call_chain
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_call_chain(start_method: str, end_method: str, max_depth: int = 8) -> str:
            """Trace the call chain between two methods."""
            max_depth = max(1, min(max_depth, 10))
            src = _cypher_escape(start_method)
            tgt = _cypher_escape(end_method)
            lines = [f"=== CALL CHAIN: {start_method} → {end_method} ===\n"]
            try:
                rows = backend.execute(
                    f"MATCH path = (a)-[:CALLS*1..{max_depth}]->(b) "
                    f"WHERE (a.name = '{src}' OR a.qualifiedName = '{src}') "
                    f"  AND (b.name = '{tgt}' OR b.qualifiedName = '{tgt}') "
                    f"RETURN nodes(path) AS chain_nodes, "
                    f"length(path) AS depth ORDER BY depth LIMIT 10"
                )
                if rows:
                    for i, r in enumerate(rows):
                        chain_nodes = r.get("chain_nodes", [])
                        chain = [n.get("qualifiedName", "?") if isinstance(n, dict) else str(n) for n in chain_nodes]
                        lines.append(f"Chain {i + 1} (depth {r.get('depth', 0)}):")
                        for j, node in enumerate(chain):
                            indent = "  " * (j + 1)
                            arrow = "→ " if j > 0 else "  "
                            lines.append(f"{indent}{arrow}{node}")
                        lines.append("")
                else:
                    lines.append(f"No call chain found within depth {max_depth}.")
            except Exception as e:
                lines.append(f"  (error: {e})")
            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_hotspots
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_hotspots(metric: str = "coupling", top_k: int = 15) -> str:
            """Identify code hotspots — highly coupled, complex, or central nodes.

            Args:
                metric: 'coupling', 'fan_in', 'fan_out', 'inheritance', or 'god_class'.
                top_k: Number of top results. Default 15.
            """
            top_k = max(1, min(top_k, 50))
            lines = [f"=== HOTSPOTS: {metric} (top {top_k}) ===\n"]

            queries = {
                "coupling": (
                    f"MATCH (n)-[r:CALLS|EXTENDS|IMPLEMENTS|OF_TYPE]->() "
                    f"WITH n, count(r) AS out_degree "
                    f"MATCH (n)<-[r2:CALLS|EXTENDS|IMPLEMENTS|OF_TYPE]-() "
                    f"WITH n, out_degree, count(r2) AS in_degree "
                    f"RETURN n.qualifiedName AS node, label(n) AS type, "
                    f"out_degree, in_degree, out_degree + in_degree AS total_coupling "
                    f"ORDER BY total_coupling DESC LIMIT {top_k}"
                ),
                "fan_in": (
                    f"MATCH (caller)-[:CALLS]->(callee) "
                    f"RETURN callee.qualifiedName AS method, label(callee) AS type, "
                    f"count(DISTINCT caller) AS fan_in "
                    f"ORDER BY fan_in DESC LIMIT {top_k}"
                ),
                "fan_out": (
                    f"MATCH (caller)-[:CALLS]->(callee) "
                    f"RETURN caller.qualifiedName AS method, label(caller) AS type, "
                    f"count(DISTINCT callee) AS fan_out "
                    f"ORDER BY fan_out DESC LIMIT {top_k}"
                ),
                "inheritance": (
                    f"MATCH path = (child)-[:EXTENDS*1..10]->(ancestor) "
                    f"WHERE NOT EXISTS {{ MATCH (ancestor)-[:EXTENDS]->() }} "
                    f"RETURN child.qualifiedName AS class, "
                    f"ancestor.qualifiedName AS root_ancestor, "
                    f"length(path) AS depth "
                    f"ORDER BY depth DESC LIMIT {top_k}"
                ),
                "god_class": (
                    f"MATCH (c:Class)-[:CONTAINS]->(member) "
                    f"WITH c, count(member) AS member_count "
                    f"RETURN c.qualifiedName AS class, member_count "
                    f"ORDER BY member_count DESC LIMIT {top_k}"
                ),
            }

            cypher = queries.get(metric)
            if not cypher:
                return f"Unknown metric '{metric}'. Use: coupling, fan_in, fan_out, inheritance, god_class."

            try:
                rows = backend.execute(cypher)
                if rows:
                    for r in rows:
                        lines.append(f"  {json.dumps(r, default=str)}")
            except Exception as e:
                lines.append(f"  (query error: {e})")

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # detect_circular_dependencies
        # ------------------------------------------------------------------ #
        @reg.tool()
        def detect_circular_dependencies() -> str:
            """Detect circular dependencies between packages and classes."""
            lines = ["=== CIRCULAR DEPENDENCY DETECTION ===\n"]

            # Package-level cycles
            try:
                rows = backend.execute(
                    "MATCH (p1:Package)-[:CONTAINS]->(c1)-[:CALLS|EXTENDS|IMPLEMENTS]->(c2)<-[:CONTAINS]-(p2:Package), "
                    "(p2)-[:CONTAINS]->(c3)-[:CALLS|EXTENDS|IMPLEMENTS]->(c4)<-[:CONTAINS]-(p1) "
                    "WHERE p1.qualifiedName <> p2.qualifiedName "
                    "RETURN DISTINCT p1.qualifiedName AS package_a, p2.qualifiedName AS package_b LIMIT 30"
                )
                if rows:
                    lines.append("── Circular Package Dependencies ──")
                    seen: set[tuple[str, str]] = set()
                    for r in rows:
                        pair = tuple(sorted([r.get("package_a", ""), r.get("package_b", "")]))
                        if pair not in seen:
                            seen.add(pair)
                            lines.append(f"  {pair[0]} <--> {pair[1]}")
                else:
                    lines.append("── No circular package dependencies detected ──")
            except Exception as e:
                lines.append(f"  (package cycle query error: {e})")

            # Class-level mutual calls
            try:
                rows = backend.execute(
                    "MATCH (a)-[:CALLS]->(b)-[:CALLS]->(a) "
                    "WHERE a.qualifiedName <> b.qualifiedName "
                    "RETURN DISTINCT a.qualifiedName AS class_a, b.qualifiedName AS class_b LIMIT 30"
                )
                if rows:
                    lines.append("\n── Mutual Call Dependencies ──")
                    seen2: set[tuple[str, str]] = set()
                    for r in rows:
                        pair = tuple(sorted([r.get("class_a", ""), r.get("class_b", "")]))
                        if pair not in seen2:
                            seen2.add(pair)
                            lines.append(f"  {pair[0]} <--> {pair[1]}")
                else:
                    lines.append("\n── No mutual call cycles detected ──")
            except Exception as e:
                lines.append(f"  (class cycle query error: {e})")

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_module_interactions
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_module_interactions(module_name: str) -> str:
            """Get all interactions a module/package has with other modules."""
            esc = _cypher_escape(module_name)
            lines = [f"=== MODULE INTERACTIONS: {module_name} ===\n"]

            # Outgoing calls
            try:
                rows = backend.execute(
                    f"MATCH (p:Package)-[:CONTAINS]->(c1)-[:CALLS]->(c2)<-[:CONTAINS]-(p2:Package) "
                    f"WHERE (p.name = '{esc}' OR p.qualifiedName = '{esc}') "
                    f"  AND p.qualifiedName <> p2.qualifiedName "
                    f"RETURN p2.qualifiedName AS target_module, count(*) AS call_count, "
                    f"collect(DISTINCT c1.name)[0..5] AS sample_callers ORDER BY call_count DESC"
                )
                if rows:
                    lines.append("── Outgoing Calls (this module → others) ──")
                    for r in rows:
                        lines.append(f"  → {r.get('target_module', '')}  ({r.get('call_count', 0)} calls)")
            except Exception as e:
                lines.append(f"  (outgoing calls error: {e})")

            # Incoming calls
            try:
                rows = backend.execute(
                    f"MATCH (p2:Package)-[:CONTAINS]->(c1)-[:CALLS]->(c2)<-[:CONTAINS]-(p:Package) "
                    f"WHERE (p.name = '{esc}' OR p.qualifiedName = '{esc}') "
                    f"  AND p.qualifiedName <> p2.qualifiedName "
                    f"RETURN p2.qualifiedName AS source_module, count(*) AS call_count, "
                    f"collect(DISTINCT c2.name)[0..5] AS sample_targets ORDER BY call_count DESC"
                )
                if rows:
                    lines.append("\n── Incoming Calls (others → this module) ──")
                    for r in rows:
                        lines.append(f"  ← {r.get('source_module', '')}  ({r.get('call_count', 0)} calls)")
            except Exception as e:
                lines.append(f"  (incoming calls error: {e})")

            # Cross-module inheritance
            try:
                rows = backend.execute(
                    f"MATCH (p:Package)-[:CONTAINS]->(c1)-[:EXTENDS|IMPLEMENTS]->(c2)<-[:CONTAINS]-(p2:Package) "
                    f"WHERE (p.name = '{esc}' OR p.qualifiedName = '{esc}') "
                    f"  AND p.qualifiedName <> p2.qualifiedName "
                    f"RETURN c1.qualifiedName AS child, c2.qualifiedName AS parent, p2.qualifiedName AS parent_module"
                )
                if rows:
                    lines.append("\n── Cross-Module Inheritance ──")
                    for r in rows:
                        lines.append(f"  {r.get('child', '')} extends/implements {r.get('parent', '')} (in {r.get('parent_module', '')})")
            except Exception as e:
                lines.append(f"  (inheritance error: {e})")

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_annotations_usage
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_annotations_usage() -> str:
            """Get a summary of all annotations/decorators used across the codebase."""
            lines = ["=== ANNOTATION USAGE SUMMARY ===\n"]
            try:
                rows = backend.execute(
                    "MATCH (target)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                    "RETURN a.name AS annotation, label(target) AS target_type, "
                    "count(*) AS usage_count ORDER BY usage_count DESC"
                )
                if rows:
                    ann_map: dict[str, list[str]] = {}
                    for r in rows:
                        ann = r.get("annotation", "")
                        ann_map.setdefault(ann, []).append(
                            f"{r.get('target_type', '')}: {r.get('usage_count', 0)}"
                        )
                    for ann, usages in sorted(ann_map.items(), key=lambda x: -sum(int(u.split(": ")[1]) for u in x[1])):
                        total = sum(int(u.split(": ")[1]) for u in usages)
                        lines.append(f"  @{ann}  (total: {total})")
                        for u in usages:
                            lines.append(f"    {u}")
                else:
                    lines.append("  No annotations found.")
            except Exception as e:
                lines.append(f"  (error: {e})")
            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_method_signature
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_method_signature(method_name: str) -> str:
            """Get the full signature of a method: return type, parameters, annotations, visibility."""
            esc = _cypher_escape(method_name)
            lines = [f"=== METHOD SIGNATURE: {method_name} ===\n"]
            try:
                rows = backend.execute(
                    f"MATCH (m:Method) WHERE m.name = '{esc}' OR m.qualifiedName = '{esc}' "
                    f"RETURN m.qualifiedName AS qualifiedName, m.name AS name, "
                    f"m.visibility AS visibility, m.returnType AS returnType, "
                    f"m.isStatic AS isStatic, m.isAbstract AS isAbstract, m.lineNumber AS line"
                )
                if not rows:
                    return f"Method '{method_name}' not found."
                for method in rows:
                    lines.append(f"  {method.get('qualifiedName', '')}")
                    for k in ["visibility", "returnType", "isStatic", "isAbstract", "line"]:
                        if method.get(k) is not None:
                            lines.append(f"  {k}: {method[k]}")
                    qn = method.get("qualifiedName", "")
                    param_rows = backend.execute(
                        f"MATCH (m:Method)-[:HAS_PARAMETER]->(p:Parameter) "
                        f"WHERE m.qualifiedName = '{_cypher_escape(qn)}' "
                        f"RETURN p.name AS name, p.type AS type ORDER BY p.lineNumber"
                    )
                    if param_rows:
                        lines.append("  parameters:")
                        for p in param_rows:
                            lines.append(f"    {p.get('type', '')} {p.get('name', '')}")
                    ann_rows = backend.execute(
                        f"MATCH (m:Method)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                        f"WHERE m.qualifiedName = '{_cypher_escape(qn)}' "
                        f"RETURN a.name AS annotation"
                    )
                    if ann_rows:
                        lines.append("  annotations:")
                        for a in ann_rows:
                            lines.append(f"    @{a.get('annotation', '')}")
                    class_rows = backend.execute(
                        f"MATCH (c)-[:CONTAINS]->(m:Method) "
                        f"WHERE m.qualifiedName = '{_cypher_escape(qn)}' "
                        f"RETURN c.qualifiedName AS class"
                    )
                    if class_rows:
                        lines.append(f"  declared in: {class_rows[0].get('class', '')}")
                    lines.append("")
            except Exception as e:
                lines.append(f"  (error: {e})")
            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_layer_classification
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_layer_classification() -> str:
            """Classify classes/modules into architectural layers using naming conventions and annotations."""
            lines = ["=== LAYER CLASSIFICATION ===\n"]

            # 1. Annotation-based classification (dynamic, framework-agnostic)
            layer_patterns = {
                "Controller/API": "(?i).*(Controller|RestController|Resource|WebServlet|Router|Blueprint|Api|Endpoint).*",
                "Service/Business": "(?i).*(Service|Component|UseCase|Interactor|Facade|Manager).*",
                "Repository/Data": "(?i).*(Repository|Dao|Store|Mapper|Gateway|Adapter).*",
                "Config": "(?i).*(Configuration|Bean|ConfigurationProperties|Settings|Config).*",
                "Security": "(?i).*(Security|Auth|Authorize|Secured|Permission).*",
                "Messaging": "(?i).*(Listener|Consumer|Subscriber|Handler|Producer|Publisher).*",
            }
            for layer, pattern in layer_patterns.items():
                try:
                    rows = backend.execute(
                        f"MATCH (c)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                        f"WHERE a.name =~ '{pattern}' "
                        f"RETURN DISTINCT c.qualifiedName AS class, a.name AS annotation"
                    )
                    if rows:
                        lines.append(f"── {layer} (by annotation) ──")
                        for r in rows:
                            lines.append(f"  @{r.get('annotation', '')} {r.get('class', '')}")
                except Exception:
                    pass

            # 2. Naming convention classification (works for all languages)
            name_patterns = {
                "Controller/API": ["Controller", "Resource", "Endpoint", "Handler", "View", "Router"],
                "Service/Business": ["Service", "Manager", "Orchestrator", "Facade", "UseCase", "Interactor"],
                "Repository/Data": ["Repository", "Repo", "Dao", "Store", "Gateway", "Mapper"],
                "Model/Domain": ["Entity", "Model", "Dto", "VO", "Request", "Response", "Schema"],
                "Config": ["Config", "Configuration", "Properties", "Settings"],
                "Utility": ["Util", "Utils", "Helper", "Helpers", "Constants", "Common"],
            }
            for layer, suffixes in name_patterns.items():
                for suffix in suffixes:
                    try:
                        rows = backend.execute(
                            f"MATCH (c) WHERE (label(c) = 'Class' OR label(c) = 'Module' OR label(c) = 'Component') "
                            f"AND c.name ENDS WITH '{suffix}' "
                            f"RETURN c.qualifiedName AS class, c.name AS name, label(c) AS type"
                        )
                        if rows:
                            lines.append(f"\n── {layer} (name *{suffix}) ──")
                            for r in rows:
                                lines.append(f"  {r.get('type', '')}: {r.get('class', '')}")
                    except Exception:
                        pass

            return "\n".join(lines) if len(lines) > 1 else "No layer classification could be determined."

        # ------------------------------------------------------------------ #
        # get_design_patterns
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_design_patterns() -> str:
            """Detect common design patterns by analyzing structural relationships."""
            lines = ["=== DESIGN PATTERN DETECTION ===\n"]

            # Singleton
            try:
                rows = backend.execute(
                    "MATCH (c:Class)-[:CONTAINS]->(con:Constructor) "
                    "WHERE con.visibility = 'private' "
                    "WITH c "
                    "MATCH (c)-[:CONTAINS]->(m:Method) "
                    "WHERE m.isStatic = true AND m.visibility = 'public' "
                    "RETURN c.qualifiedName AS class, collect(m.name) AS static_methods LIMIT 20"
                )
                if rows:
                    lines.append("── Possible Singletons ──")
                    for r in rows:
                        lines.append(f"  {r.get('class', '')}  methods: {r.get('static_methods', [])}")
            except Exception as e:
                lines.append(f"  (singleton detection error: {e})")

            # Factory
            try:
                rows = backend.execute(
                    "MATCH (c:Class)-[:CONTAINS]->(m:Method)-[:RETURNS]->(t) "
                    "WHERE (label(t) = 'Interface' OR (label(t) = 'Class' AND t.isAbstract = true)) "
                    "RETURN c.qualifiedName AS factory, m.name AS method, t.qualifiedName AS product LIMIT 20"
                )
                if rows:
                    lines.append("\n── Possible Factories ──")
                    for r in rows:
                        lines.append(f"  {r.get('factory', '')}.{r.get('method', '')} → {r.get('product', '')}")
            except Exception as e:
                lines.append(f"  (factory detection error: {e})")

            # Strategy
            try:
                rows = backend.execute(
                    "MATCH (impl:Class)-[:IMPLEMENTS]->(i:Interface) "
                    "WITH i, count(impl) AS impl_count WHERE impl_count >= 2 "
                    "MATCH (consumer:Class)-[:CONTAINS]->(f:Field)-[:OF_TYPE]->(i) "
                    "RETURN i.qualifiedName AS strategy_interface, impl_count, "
                    "consumer.qualifiedName AS consumer LIMIT 20"
                )
                if rows:
                    lines.append("\n── Possible Strategy Pattern ──")
                    for r in rows:
                        lines.append(
                            f"  Interface: {r.get('strategy_interface', '')} "
                            f"({r.get('impl_count', 0)} impls) → used by {r.get('consumer', '')}"
                        )
            except Exception as e:
                lines.append(f"  (strategy detection error: {e})")

            # Template Method
            try:
                rows = backend.execute(
                    "MATCH (c:Class)-[:CONTAINS]->(m:Method) WHERE c.isAbstract = true "
                    "WITH c, "
                    "  sum(CASE WHEN m.isAbstract = true THEN 1 ELSE 0 END) AS abstract_methods, "
                    "  sum(CASE WHEN m.isAbstract = false OR m.isAbstract IS NULL THEN 1 ELSE 0 END) AS concrete_methods "
                    "WHERE abstract_methods >= 1 AND concrete_methods >= 1 "
                    "RETURN c.qualifiedName AS template_class, abstract_methods, concrete_methods LIMIT 20"
                )
                if rows:
                    lines.append("\n── Possible Template Method ──")
                    for r in rows:
                        lines.append(
                            f"  {r.get('template_class', '')} "
                            f"(abstract: {r.get('abstract_methods', 0)}, concrete: {r.get('concrete_methods', 0)})"
                        )
            except Exception as e:
                lines.append(f"  (template method detection error: {e})")

            # Observer
            try:
                rows = backend.execute(
                    "MATCH (c) WHERE c.name CONTAINS 'Listener' OR c.name CONTAINS 'Observer' "
                    "   OR c.name CONTAINS 'EventHandler' OR c.name CONTAINS 'Subscriber' "
                    "RETURN c.qualifiedName AS class, c.name AS name, label(c) AS type ORDER BY c.name"
                )
                if rows:
                    lines.append("\n── Possible Observers/Listeners ──")
                    for r in rows:
                        lines.append(f"  {r.get('type', '')}: {r.get('class', '')}")
            except Exception as e:
                lines.append(f"  (observer detection error: {e})")

            return "\n".join(lines) if len(lines) > 1 else "No design patterns detected."

        # ------------------------------------------------------------------ #
        # get_domain_model
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_domain_model() -> str:
            """Extract the domain model: entity classes, their fields, and relationships."""
            lines = ["=== DOMAIN MODEL ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (c:Class)-[:CONTAINS]->(f:Field)-[:OF_TYPE]->(t:Class) "
                    "RETURN c.qualifiedName AS owner_class, c.name AS owner_name, "
                    "f.name AS field_name, t.qualifiedName AS field_type, t.name AS type_name "
                    "ORDER BY c.qualifiedName, f.name"
                )
                if rows:
                    lines.append("── Entity Relationships (class → field → type) ──")
                    current_class = None
                    for r in rows:
                        owner = r.get("owner_class", "")
                        if owner != current_class:
                            current_class = owner
                            lines.append(f"\n  {owner}")
                        lines.append(f"    .{r.get('field_name', '')} : {r.get('type_name', '')}")
                else:
                    lines.append("  No field-type relationships found.")
            except Exception as e:
                lines.append(f"  (domain model error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (c:Class)-[:IMPLEMENTS]->(i:Interface) "
                    "RETURN i.qualifiedName AS interface, i.name AS interface_name, "
                    "collect(c.qualifiedName) AS implementors ORDER BY interface"
                )
                if rows:
                    lines.append("\n── Interface Implementations ──")
                    for r in rows:
                        impls = r.get("implementors", [])
                        lines.append(f"  {r.get('interface', '')}")
                        for impl in (impls if isinstance(impls, list) else [impls]):
                            lines.append(f"    implemented by: {impl}")
            except Exception as e:
                lines.append(f"  (interface query error: {e})")

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_scheduled_jobs
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_scheduled_jobs() -> str:
            """Find scheduled/background tasks, event listeners, and async handlers across frameworks."""
            lines = ["=== SCHEDULED JOBS & BACKGROUND TASKS ===\n"]

            # Dynamic annotation discovery for scheduling/async/event patterns
            try:
                rows = backend.execute(
                    "MATCH (m)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                    "WHERE a.name =~ '(?i).*(Scheduled|Async|Cron|Timer|EventListener|Listener|"
                    "Subscriber|Consumer|Handler|Retryable|Cacheable|Task|Worker|Job|Celery|periodic).*' "
                    "RETURN m.qualifiedName AS method, m.name AS name, "
                    "a.name AS annotation, m.lineNumber AS line, label(m) AS type "
                    "ORDER BY annotation, method"
                )
                if rows:
                    lines.append("── Annotated Background Tasks ──")
                    for r in rows:
                        lines.append(
                            f"  @{r.get('annotation', '')} {r.get('type', '')}: "
                            f"{r.get('method', '')}  (line {r.get('line', '')})"
                        )
                else:
                    lines.append("  No scheduled jobs or background tasks detected.")
            except Exception as e:
                lines.append(f"  (annotation search error: {e})")

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_exception_handling
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_exception_handling() -> str:
            """Analyze exception handling patterns: THROWS relationships and exception hierarchy."""
            lines = ["=== EXCEPTION HANDLING ===\n"]
            try:
                rows = backend.execute(
                    "MATCH (m:Method)-[:THROWS]->(e:Class) "
                    "RETURN m.qualifiedName AS method, e.qualifiedName AS exception "
                    "ORDER BY e.qualifiedName, m.qualifiedName"
                )
                if rows:
                    lines.append("── Methods That Throw Exceptions ──")
                    exc_map: dict[str, list[str]] = {}
                    for r in rows:
                        exc = r.get("exception", "")
                        exc_map.setdefault(exc, []).append(r.get("method", ""))
                    for exc, methods in sorted(exc_map.items()):
                        lines.append(f"  {exc}:")
                        for m in methods:
                            lines.append(f"    thrown by: {m}")
                else:
                    lines.append("  No THROWS relationships found.")
            except Exception as e:
                lines.append(f"  (throws query error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (e:Class)-[:EXTENDS*1..5]->(parent:Class) "
                    "WHERE parent.name CONTAINS 'Exception' OR parent.name CONTAINS 'Error' "
                    "   OR e.name CONTAINS 'Exception' OR e.name CONTAINS 'Error' "
                    "RETURN e.qualifiedName AS exception, parent.qualifiedName AS parent "
                    "ORDER BY parent, exception"
                )
                if rows:
                    lines.append("\n── Exception Class Hierarchy ──")
                    for r in rows:
                        lines.append(f"  {r.get('exception', '')} extends {r.get('parent', '')}")
            except Exception as e:
                lines.append(f"  (hierarchy query error: {e})")

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_external_dependencies
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_external_dependencies() -> str:
            """Find references to external types from outside the analyzed codebase."""
            lines = ["=== EXTERNAL DEPENDENCIES ===\n"]
            try:
                rows = backend.execute(
                    "MATCH (n) WHERE n.external = true "
                    "RETURN n.qualifiedName AS name, label(n) AS type, n.name AS short_name "
                    "ORDER BY n.qualifiedName"
                )
                if rows:
                    pkg_map: dict[str, list[dict]] = {}
                    for r in rows:
                        qn = r.get("name", "")
                        parts = qn.split(".")
                        top_pkg = ".".join(parts[: min(3, len(parts) - 1)]) if len(parts) > 1 else qn
                        pkg_map.setdefault(top_pkg, []).append(r)
                    for pkg, items in sorted(pkg_map.items()):
                        lines.append(f"  {pkg}  ({len(items)} types)")
                        for item in items[:5]:
                            lines.append(f"    {item.get('type', '')}: {item.get('name', '')}")
                        if len(items) > 5:
                            lines.append(f"    ... and {len(items) - 5} more")
                else:
                    lines.append("  No external dependencies marked in the graph.")
            except Exception as e:
                lines.append(f"  (error: {e})")
            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_unused_code
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_unused_code() -> str:
            """Find potentially unused code: uncalled methods, unreferenced classes, unimplemented interfaces."""
            lines = ["=== POTENTIALLY UNUSED CODE ===\n"]

            try:
                rows = backend.execute(
                    "MATCH (m:Method) "
                    "WHERE m.visibility IN ['public', 'protected'] AND m.name <> 'main' "
                    "AND NOT EXISTS { MATCH ()-[:CALLS]->(m) } "
                    "AND NOT EXISTS { MATCH ()-[:OVERRIDES]->(m) } "
                    "RETURN m.qualifiedName AS method, m.visibility AS visibility "
                    "ORDER BY m.qualifiedName LIMIT 40"
                )
                if rows:
                    lines.append(f"── Public/Protected Methods With No Callers ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  [{r.get('visibility', '')}] {r.get('method', '')}")
            except Exception as e:
                lines.append(f"  (unused methods error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (c:Class) "
                    "WHERE NOT EXISTS { MATCH ()-[:EXTENDS|IMPLEMENTS|OF_TYPE|CALLS]->(c) } "
                    "AND NOT EXISTS { MATCH ()-[:CONTAINS]->(c)-[:HAS_ANNOTATION]->() } "
                    "RETURN c.qualifiedName AS class ORDER BY c.qualifiedName LIMIT 30"
                )
                if rows:
                    lines.append(f"\n── Classes With No Incoming References ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  {r.get('class', '')}")
            except Exception as e:
                lines.append(f"  (unused classes error: {e})")

            try:
                rows = backend.execute(
                    "MATCH (i:Interface) WHERE NOT EXISTS { MATCH ()-[:IMPLEMENTS]->(i) } "
                    "RETURN i.qualifiedName AS interface ORDER BY i.qualifiedName LIMIT 20"
                )
                if rows:
                    lines.append(f"\n── Interfaces With No Implementors ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  {r.get('interface', '')}")
            except Exception as e:
                lines.append(f"  (unimplemented interfaces error: {e})")

            return "\n".join(lines) if len(lines) > 1 else "No unused code detected."

        # ------------------------------------------------------------------ #
        # get_component_coupling_matrix
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_component_coupling_matrix() -> str:
            """Generate a coupling matrix between all packages/modules."""
            lines = ["=== COMPONENT COUPLING MATRIX ===\n"]
            try:
                rows = backend.execute(
                    "MATCH (p1:Package)-[:CONTAINS]->(c1)-[r:CALLS|EXTENDS|IMPLEMENTS|OF_TYPE]->(c2)<-[:CONTAINS]-(p2:Package) "
                    "WHERE p1.qualifiedName <> p2.qualifiedName "
                    "RETURN p1.qualifiedName AS source, p2.qualifiedName AS target, "
                    "count(r) AS weight, collect(DISTINCT label(r)) AS rel_types "
                    "ORDER BY weight DESC"
                )
                if rows:
                    packages: set[str] = set()
                    matrix: dict[tuple[str, str], dict] = {}
                    for r in rows:
                        src = r.get("source", "")
                        tgt = r.get("target", "")
                        packages.add(src)
                        packages.add(tgt)
                        matrix[(src, tgt)] = {"weight": r.get("weight", 0), "types": r.get("rel_types", [])}

                    lines.append(f"Packages involved: {len(packages)}")
                    lines.append(f"Total dependency links: {len(rows)}\n")
                    lines.append("── Dependencies (source → target : weight) ──")
                    for r in rows:
                        types_str = ", ".join(r.get("rel_types", []))
                        lines.append(f"  {r.get('source', '')} → {r.get('target', '')} : {r.get('weight', 0)}  [{types_str}]")

                    lines.append("\n── Bidirectional Coupling ──")
                    seen: set[tuple[str, str]] = set()
                    found_bidir = False
                    for (src, tgt), info in matrix.items():
                        pair = tuple(sorted([src, tgt]))
                        if pair not in seen and (tgt, src) in matrix:
                            seen.add(pair)
                            found_bidir = True
                            lines.append(f"  {src} ↔ {tgt}  (→ {info['weight']}, ← {matrix[(tgt, src)]['weight']})")
                    if not found_bidir:
                        lines.append("  None detected — good separation of concerns.")
                else:
                    lines.append("  No cross-package dependencies found.")
            except Exception as e:
                lines.append(f"  (error: {e})")
            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # impact_analysis
        # ------------------------------------------------------------------ #
        @reg.tool()
        def impact_analysis(node_name: str, depth: int = 3) -> str:
            """If this node changes, what else is affected? Shows transitive dependents.

            Args:
                node_name: Name or qualified name of the class/method/interface.
                depth: How deep to trace (1-6). Default 3.
            """
            depth = max(1, min(depth, 6))
            esc = _cypher_escape(node_name)
            lines = [f"=== IMPACT ANALYSIS: {node_name} (depth={depth}) ===\n"]

            # Direct callers
            try:
                rows = backend.execute(
                    f"MATCH (caller)-[:CALLS]->(target) "
                    f"WHERE target.name = '{esc}' OR target.qualifiedName = '{esc}' "
                    f"RETURN DISTINCT caller.qualifiedName AS affected, label(caller) AS type"
                )
                if rows:
                    lines.append(f"── Direct Callers ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  {r.get('type', '')}: {r.get('affected', '')}")
            except Exception as e:
                lines.append(f"  (direct callers error: {e})")

            # Transitive callers
            try:
                rows = backend.execute(
                    f"MATCH path = (caller)-[:CALLS*2..{depth}]->(target) "
                    f"WHERE target.name = '{esc}' OR target.qualifiedName = '{esc}' "
                    f"RETURN DISTINCT caller.qualifiedName AS affected, "
                    f"label(caller) AS type, length(path) AS distance "
                    f"ORDER BY distance LIMIT 50"
                )
                if rows:
                    lines.append(f"\n── Transitive Callers ({len(rows)}, depth 2-{depth}) ──")
                    for r in rows:
                        lines.append(f"  {'  ' * r.get('distance', 1)}{r.get('type', '')}: {r.get('affected', '')}")
            except Exception as e:
                lines.append(f"  (transitive callers error: {e})")

            # Subclasses / implementors
            try:
                rows = backend.execute(
                    f"MATCH (child)-[:EXTENDS|IMPLEMENTS*1..{depth}]->(target) "
                    f"WHERE target.name = '{esc}' OR target.qualifiedName = '{esc}' "
                    f"RETURN DISTINCT child.qualifiedName AS affected, label(child) AS type"
                )
                if rows:
                    lines.append(f"\n── Subclasses / Implementors ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  {r.get('type', '')}: {r.get('affected', '')}")
            except Exception as e:
                lines.append(f"  (subclass query error: {e})")

            # Overriders
            try:
                rows = backend.execute(
                    f"MATCH (child)-[:OVERRIDES]->(target) "
                    f"WHERE target.name = '{esc}' OR target.qualifiedName = '{esc}' "
                    f"RETURN DISTINCT child.qualifiedName AS affected"
                )
                if rows:
                    lines.append(f"\n── Methods Overriding This ({len(rows)}) ──")
                    for r in rows:
                        lines.append(f"  {r.get('affected', '')}")
            except Exception:
                pass

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # COMPOSITE TOOLS — reduce LLM round-trips
        # ------------------------------------------------------------------ #

        @reg.tool()
        def get_architecture_summary() -> str:
            """One-shot architecture overview: schema + summary + architecture + layer classification. Call this first."""
            try:
                coverage_rows = backend.execute(
                    "MATCH (n) WHERE n.language IS NOT NULL "
                    "RETURN n.language AS language, count(n) AS count ORDER BY count DESC"
                )
                coverage = _format_rows(coverage_rows) if coverage_rows else "No language-tagged nodes."
            except Exception:
                coverage = "No language-tagged nodes."

            parts = [
                self.registry.call("get_schema"),
                "=== LANGUAGE COVERAGE ===\n" + coverage,
                self.registry.call("summary"),
                self.registry.call("get_architecture_overview"),
                self.registry.call("get_layer_classification"),
            ]
            return "\n\n".join(parts)

        @reg.tool()
        def get_module_deep_dive(module_name: str) -> str:
            """Deep-dive into a package/module: contents + interactions + hotspots in one call.

            Args:
                module_name: Name or qualified name of the package/module.
            """
            parts = [
                self.registry.call("get_package_contents", package_name=module_name),
                self.registry.call("get_module_interactions", module_name=module_name),
                self.registry.call("get_hotspots", metric="coupling", top_k=10),
            ]
            return "\n\n".join(parts)

        @reg.tool()
        def trace_user_flow(entry_point: str, max_depth: int = 5) -> str:
            """Trace a complete user flow from an entry point through all layers of the system.

            Combines call chain traversal, data flow analysis, class ownership, and
            source file locations to give a full E2E picture of a single flow. Use
            this as the primary tool when generating test scenarios.

            Args:
                entry_point: Name or qualifiedName of the entry method/function/endpoint.
                max_depth: Max call depth to traverse (1-8). Default 5.
            """
            max_depth = max(1, min(max_depth, 8))
            esc = _cypher_escape(entry_point)
            lines = [f"=== USER FLOW TRACE: {entry_point} (depth={max_depth}) ===\n"]

            # ── 1. Entry point details ──────────────────────────────────────
            try:
                rows = backend.execute(
                    f"MATCH (m) "
                    f"WHERE m.name = '{esc}' OR m.qualifiedName = '{esc}' "
                    f"OPTIONAL MATCH (m)-[:SOURCE_FILE]->(f:File) "
                    f"OPTIONAL MATCH (owner)-[:CONTAINS]->(m) "
                    f"RETURN m.qualifiedName AS qualifiedName, label(m) AS type, "
                    f"m.visibility AS visibility, m.returnType AS returnType, "
                    f"m.lineNumber AS line, f.path AS file_path, "
                    f"owner.qualifiedName AS owner LIMIT 1"
                )
                if rows:
                    r = rows[0]
                    lines.append("── Entry Point ──")
                    lines.append(f"  Name:       {r.get('qualifiedName', entry_point)}")
                    lines.append(f"  Type:       {r.get('type', '')}")
                    lines.append(f"  Owner:      {r.get('owner', 'N/A')}")
                    lines.append(f"  Visibility: {r.get('visibility', '')}")
                    lines.append(f"  Returns:    {r.get('returnType', '')}")
                    lines.append(f"  Line:       {r.get('line', '')}")
                    lines.append(f"  File:       {r.get('file_path', 'N/A')}")
            except Exception as e:
                lines.append(f"  (entry point lookup error: {e})")

            # ── 2. Annotations on the entry point ──────────────────────────
            try:
                rows = backend.execute(
                    f"MATCH (m)-[:HAS_ANNOTATION]->(a:AnnotationType) "
                    f"WHERE m.name = '{esc}' OR m.qualifiedName = '{esc}' "
                    f"RETURN a.name AS annotation LIMIT 20"
                )
                if rows:
                    annotations = [r.get("annotation", "") for r in rows]
                    lines.append(f"\n── Annotations: {', '.join(annotations)} ──")
            except Exception:
                pass

            # ── 3. Parameters & return type ────────────────────────────────
            try:
                rows = backend.execute(
                    f"MATCH (m)-[:HAS_PARAMETER]->(p:Parameter) "
                    f"WHERE m.name = '{esc}' OR m.qualifiedName = '{esc}' "
                    f"OPTIONAL MATCH (p)-[:OF_TYPE]->(t) "
                    f"RETURN p.name AS param, t.name AS param_type ORDER BY p.name"
                )
                if rows:
                    lines.append("\n── Parameters ──")
                    for r in rows:
                        lines.append(f"  {r.get('param', '?')}: {r.get('param_type', 'unknown')}")
            except Exception:
                pass

            # ── 4. Full call chain (all reachable methods) ─────────────────
            try:
                rows = backend.execute(
                    f"MATCH path = (start)-[:CALLS*1..{max_depth}]->(called) "
                    f"WHERE start.name = '{esc}' OR start.qualifiedName = '{esc}' "
                    f"RETURN DISTINCT called.qualifiedName AS called_method, "
                    f"label(called) AS type, length(path) AS depth "
                    f"ORDER BY depth, called_method LIMIT 100"
                )
                if rows:
                    lines.append("\n── Call Chain (reachable methods) ──")
                    cur_depth = -1
                    for r in rows:
                        d = r.get("depth", 0)
                        if d != cur_depth:
                            lines.append(f"\n  [depth {d}]")
                            cur_depth = d
                        lines.append(f"    {r.get('type', '')}: {r.get('called_method', '')}")
                else:
                    lines.append("\n── Call Chain: no outgoing calls found ──")
            except Exception as e:
                lines.append(f"\n  (call chain error: {e})")

            # ── 5. External service / repository boundaries ─────────────────
            try:
                rows = backend.execute(
                    f"MATCH (start)-[:CALLS*1..{max_depth}]->(called) "
                    f"WHERE (start.name = '{esc}' OR start.qualifiedName = '{esc}') "
                    f"  AND (called.qualifiedName =~ '(?i).*(repository|dao|client|gateway|adapter|kafka|rabbit|redis|cache|db|http|rest|feign|grpc).*') "
                    f"RETURN DISTINCT called.qualifiedName AS boundary, label(called) AS type "
                    f"ORDER BY type, boundary LIMIT 50"
                )
                if rows:
                    lines.append("\n── External Boundaries (repos / clients / adapters) ──")
                    for r in rows:
                        lines.append(f"  {r.get('type', '')}: {r.get('boundary', '')}")
            except Exception:
                pass

            # ── 6. Exception types thrown along the flow ────────────────────
            try:
                rows = backend.execute(
                    f"MATCH (start)-[:CALLS*0..{max_depth}]->(m)-[:THROWS]->(ex) "
                    f"WHERE start.name = '{esc}' OR start.qualifiedName = '{esc}' "
                    f"RETURN DISTINCT ex.name AS exception, m.qualifiedName AS thrown_by "
                    f"ORDER BY exception LIMIT 30"
                )
                if rows:
                    lines.append("\n── Exceptions Thrown ──")
                    for r in rows:
                        lines.append(f"  {r.get('exception', '')} (from {r.get('thrown_by', '')})")
            except Exception:
                pass

            # ── 7. Source files involved ────────────────────────────────────
            try:
                rows = backend.execute(
                    f"MATCH (start)-[:CALLS*0..{max_depth}]->(called) "
                    f"WHERE start.name = '{esc}' OR start.qualifiedName = '{esc}' "
                    f"OPTIONAL MATCH (called)-[:SOURCE_FILE]->(f:File) "
                    f"WITH f.path AS file_path WHERE file_path IS NOT NULL "
                    f"RETURN DISTINCT file_path ORDER BY file_path LIMIT 50"
                )
                if rows:
                    lines.append("\n── Source Files Involved ──")
                    for r in rows:
                        lines.append(f"  {r.get('file_path', '')}")
            except Exception:
                pass

            return "\n".join(lines)

        # ------------------------------------------------------------------ #
        # get_method_source
        # ------------------------------------------------------------------ #
        @reg.tool()
        def get_method_source(method_name: str) -> str:
            """Read the source code of a specific method using graph metadata.

            Queries the graph for the method's file path and line numbers, then
            reads ONLY those lines — far more token-efficient than reading entire
            files. Use after graph tools have identified the method as worth
            inspecting (e.g. after get_class_details, get_callers, get_hotspots).

            Subject to a per-run source-read budget (default 15 calls). Once the
            budget is exhausted the tool returns a budget-exceeded message.

            Args:
                method_name: Name or qualified name of the method/function.
            """
            if not repo_path:
                return (
                    "Source reading is not available: repo_path was not configured. "
                    "Use graph tools (get_class_details, execute_cypher) for analysis."
                )

            esc = _cypher_escape(method_name)

            # 1. Try SOURCE_FILE relationship first (most reliable)
            rows = []
            for cypher in [
                # Methods / Functions with SOURCE_FILE edge
                (
                    f"MATCH (m)-[:SOURCE_FILE]->(f:File) "
                    f"WHERE (label(m) = 'Method' OR label(m) = 'Function' OR "
                    f"       label(m) = 'Constructor' OR label(m) = 'ArrowFunction' OR "
                    f"       label(m) = 'Component' OR label(m) = 'Hook' OR label(m) = 'AsyncFunction') "
                    f"AND (m.name = '{esc}' OR m.qualifiedName = '{esc}') "
                    f"RETURN f.path AS path, m.lineNumber AS start_line, "
                    f"m.endLineNumber AS end_line, m.qualifiedName AS qualified_name "
                    f"LIMIT 3"
                ),
                # Fallback: path property directly on the node
                (
                    f"MATCH (m) WHERE (label(m) = 'Method' OR label(m) = 'Function' OR "
                    f"                 label(m) = 'Constructor' OR label(m) = 'ArrowFunction' OR "
                    f"                 label(m) = 'Component' OR label(m) = 'Hook' OR label(m) = 'AsyncFunction') "
                    f"AND (m.name = '{esc}' OR m.qualifiedName = '{esc}') "
                    f"AND m.path IS NOT NULL "
                    f"RETURN m.path AS path, m.lineNumber AS start_line, "
                    f"m.endLineNumber AS end_line, m.qualifiedName AS qualified_name "
                    f"LIMIT 3"
                ),
            ]:
                try:
                    rows = backend.execute(cypher)
                    if rows:
                        break
                except Exception:
                    pass

            if not rows:
                return (
                    f"Method '{method_name}' not found in graph or has no file path. "
                    f"Try: search_nodes('{method_name}', 'Method') to verify the name."
                )

            results = []
            repo_root = Path(repo_path).resolve()

            for row in rows[:2]:  # max 2 matches (overloaded methods)
                file_path = row.get("path")
                start_line = row.get("start_line")
                end_line = row.get("end_line")
                qual_name = row.get("qualified_name") or method_name

                if not file_path:
                    results.append(f"// {qual_name}: no file path in graph")
                    continue

                # Resolve against repo root
                candidate = Path(file_path)
                if not candidate.is_absolute():
                    candidate = repo_root / file_path
                try:
                    candidate = candidate.resolve()
                    candidate.relative_to(repo_root)  # security: stay in repo
                except ValueError:
                    results.append(f"// {qual_name}: path outside repo root — skipped")
                    continue

                if not candidate.exists():
                    results.append(f"// {qual_name}: file not found: {file_path}")
                    continue

                if not start_line:
                    results.append(f"// {qual_name}: no line number in graph — cannot extract")
                    continue

                try:
                    all_lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                    s = max(0, int(start_line) - 1)         # 0-indexed
                    e = int(end_line) if end_line else s + 80
                    e = min(e, s + 150, len(all_lines))     # hard cap: 150 lines
                    snippet = "\n".join(
                        f"{s + i + 1:4d}  {line}" for i, line in enumerate(all_lines[s:e])
                    )
                    results.append(
                        f"// {qual_name}  ({candidate.name} lines {s+1}–{e})\n{snippet}"
                    )
                except Exception as ex:
                    results.append(f"// {qual_name}: read error: {ex}")

            return "\n\n".join(results) if results else f"No source extracted for '{method_name}'."

        # ------------------------------------------------------------------ #
        # execute_cypher — generic read-only query tool
        # ------------------------------------------------------------------ #
        _MUTATION_KW = re.compile(
            r"\b(CREATE|DELETE|DETACH|SET|REMOVE|MERGE|DROP|ALTER|COPY|IMPORT)\b",
            re.IGNORECASE,
        )

        @reg.tool()
        def execute_cypher(query: str, limit: int = 50) -> str:
            """Execute a read-only Cypher query against the knowledge graph.

            Use this when the predefined tools don't cover your specific query.
            KuzuDB Cypher dialect notes:
            - Use label(n) not labels(n)[0] to get a node's label
            - Use label(r) not type(r) for relationship types
            - No shortestPath(); use MATCH path = (a)-[*1..N]->(b) ORDER BY length(path) LIMIT 1
            - After DISTINCT/aggregation, ORDER BY must use column aliases not original variable names
            - Multi-label check: use label(n) = 'X' OR label(n) = 'Y' (not n:X OR n:Y in WHERE)

            Args:
                query: A Cypher READ query (MATCH/RETURN). Mutations are rejected.
                limit: Max rows to return (1-200). Default 50.
            """
            if _MUTATION_KW.search(query):
                return "Error: only read-only queries (MATCH/RETURN) are allowed."
            limit = max(1, min(limit, 200))
            # Append LIMIT if not already present
            stripped = query.rstrip().rstrip(";")
            if not re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
                stripped += f" LIMIT {limit}"
            try:
                rows = backend.execute(stripped)
                if not rows:
                    return "Query returned 0 rows."
                return _format_rows(rows[:limit])
            except Exception as e:
                return f"Cypher error: {e}"

    # ── Public convenience methods ──────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """Return all registered tools as dicts for documentation / LLM tool descriptions."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self.registry.list_tools()
        ]

    def call(self, tool_name: str, **kwargs) -> str:
        """Call a tool by name with keyword arguments."""
        return self.registry.call(tool_name, **kwargs)

    def call_json(self, tool_name: str, args_json: str = "{}") -> str:
        """Call a tool by name with a JSON string of arguments."""
        kwargs = json.loads(args_json)
        return self.registry.call(tool_name, **kwargs)

    def openai_tool_definitions(self) -> list[dict]:
        """Return tool definitions in OpenAI function-calling format.
        Ready to pass directly into the `tools` parameter of the Chat Completions API.

        Example:
            toolkit = ReverseEngineerToolkit(backend)
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[...],
                tools=toolkit.openai_tool_definitions(),
            )
        """
        tools = []
        for t in self.registry.list_tools():
            properties = {}
            required = []
            for pname, pinfo in t.parameters.items():
                prop: dict[str, Any] = {"type": pinfo["type"]}
                if "default" in pinfo:
                    prop["default"] = pinfo["default"]
                else:
                    required.append(pname)
                properties[pname] = prop
            tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description.split("\n")[0],  # first line
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return tools

    def anthropic_tool_definitions(self) -> list[dict]:
        """Return tool definitions in Anthropic tool-use format.
        Ready to pass into the `tools` parameter of the Messages API.

        Example:
            toolkit = ReverseEngineerToolkit(backend)
            response = anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                messages=[...],
                tools=toolkit.anthropic_tool_definitions(),
            )
        """
        tools = []
        for t in self.registry.list_tools():
            properties = {}
            required = []
            for pname, pinfo in t.parameters.items():
                prop: dict[str, Any] = {"type": pinfo["type"]}
                if "default" in pinfo:
                    prop["default"] = pinfo["default"]
                else:
                    required.append(pname)
                properties[pname] = prop
            tools.append({
                "name": t.name,
                "description": t.description.split("\n")[0],
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            })
        return tools

    def handle_tool_call(self, tool_call: dict) -> dict:
        """Handle an OpenAI-style tool_call dict and return the result.
        Works with both OpenAI and Anthropic response formats.

        Args:
            tool_call: Dict with 'function.name' and 'function.arguments' (OpenAI)
                       or 'name' and 'input' (Anthropic).

        Returns:
            Dict with 'tool_call_id', 'role', 'content' ready to append to messages.
        """
        # OpenAI format
        if "function" in tool_call:
            name = tool_call["function"]["name"]
            args = tool_call["function"].get("arguments", "{}")
            if isinstance(args, str):
                args = json.loads(args)
            call_id = tool_call.get("id", "")
        # Anthropic format
        elif "name" in tool_call:
            name = tool_call["name"]
            args = tool_call.get("input", {})
            call_id = tool_call.get("id", "")
        else:
            return {"error": "Unknown tool_call format"}

        result = self.call(name, **args)
        return {
            "tool_call_id": call_id,
            "role": "tool",
            "content": result,
        }

    def run_batch(self, tool_calls: list[dict]) -> list[dict]:
        """Execute a batch of tool calls sequentially.

        Args:
            tool_calls: List of {"tool": "tool_name", "args": {...}} dicts.

        Returns:
            List of {"tool": name, "args": args, "result": result} dicts.
        """
        results = []
        for call in tool_calls:
            name = call.get("tool", "")
            args = call.get("args", {})
            result = self.call(name, **args)
            results.append({"tool": name, "args": args, "result": result})
        return results

    def run_re_workflow(self, verbose: bool = True) -> dict[str, str]:
        """Execute the standard 5-phase reverse engineering workflow automatically.
        Returns a dict of {phase_name: combined_results}.

        This is the headless/automated version of the agent workflow defined
        in the agent.md file. Useful for generating a baseline analysis without
        an LLM in the loop.
        """
        results: dict[str, str] = {}
        def _log(msg: str):
            if verbose:
                print(f"  ⟫ {msg}", file=sys.stderr)

        # Phase 1: Orientation
        _log("Phase 1: Orientation")
        phase1_parts = []
        phase1_parts.append(self.call("get_schema"))
        phase1_parts.append(self.call("summary"))
        phase1_parts.append(self.call("get_architecture_overview"))
        phase1_parts.append(self.call("get_layer_classification"))
        results["orientation"] = "\n\n".join(phase1_parts)

        # Phase 2: Entry Points & API Surface
        _log("Phase 2: Entry Points & API Surface")
        phase2_parts = []
        phase2_parts.append(self.call("get_entry_points"))
        phase2_parts.append(self.call("get_api_endpoints"))
        phase2_parts.append(self.call("get_scheduled_jobs"))
        phase2_parts.append(self.call("get_annotations_usage"))
        results["api_surface"] = "\n\n".join(phase2_parts)

        # Phase 3: Domain & Patterns
        _log("Phase 3: Domain & Patterns")
        phase3_parts = []
        phase3_parts.append(self.call("get_domain_model"))
        phase3_parts.append(self.call("get_design_patterns"))
        phase3_parts.append(self.call("get_component_coupling_matrix"))
        results["domain_patterns"] = "\n\n".join(phase3_parts)

        # Phase 4: Quality & Risk
        _log("Phase 4: Quality & Risk")
        phase4_parts = []
        for metric in ["coupling", "fan_in", "fan_out", "god_class"]:
            phase4_parts.append(self.call("get_hotspots", metric=metric, top_k=10))
        phase4_parts.append(self.call("detect_circular_dependencies"))
        phase4_parts.append(self.call("get_unused_code"))
        phase4_parts.append(self.call("get_external_dependencies"))
        phase4_parts.append(self.call("get_exception_handling"))
        results["quality_risk"] = "\n\n".join(phase4_parts)

        # Phase 5: Module deep-dives (discover packages from architecture overview)
        _log("Phase 5: Module Deep-Dives")
        phase5_parts = []
        try:
            pkg_rows = self.backend.execute(
                "MATCH (p:Package) RETURN p.qualifiedName AS pkg ORDER BY p.qualifiedName"
            )
            for row in pkg_rows[:20]:  # cap at 20 packages
                pkg = row.get("pkg", "")
                if pkg:
                    _log(f"  Analyzing package: {pkg}")
                    phase5_parts.append(f"--- Package: {pkg} ---")
                    phase5_parts.append(self.call("get_package_contents", package_name=pkg))
                    phase5_parts.append(self.call("get_module_interactions", module_name=pkg))
        except Exception as e:
            phase5_parts.append(f"(module deep-dive error: {e})")
        results["module_deep_dives"] = "\n\n".join(phase5_parts)

        _log("Done — all 5 phases complete.")
        return results
