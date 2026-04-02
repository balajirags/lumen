#!/usr/bin/env python3
"""
MCP server for code-mem-graph.
Exposes the Code Property Graph stored in KuzuDB or Neo4j as MCP tools.

Usage:
  cmg-mcp --db-path ./bin/kuzu_db/my-project-db
  cmg-mcp --backend neo4j --neo4j-uri bolt://localhost:7687 --neo4j-password changeme
"""

import argparse
import os
import sys
import json
import re
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

# ── Graph backend wrappers ──────────────────────────────────────────────────

NODE_TYPES = [
    'Package', 'Class', 'Interface', 'Enum', 'Record', 'AnnotationType',
    'Method', 'Constructor', 'Field', 'Parameter', 'File', 'Statement',
    'Module', 'Function', 'ArrowFunction', 'Component', 'Hook', 'JSXElement',
    'Decorator', 'Generator', 'AsyncFunction', 'Comprehension',
    'DataClass', 'SealedClass', 'SealedInterface', 'ObjectDecl',
    'CompanionObject', 'ExtensionFunction', 'SuspendFunction',
    'Property', 'Lambda', 'InitBlock', 'TypeAlias',
]

REL_TYPES = [
    'CONTAINS', 'EXTENDS', 'IMPLEMENTS', 'CALLS', 'RETURNS',
    'HAS_PARAMETER', 'OF_TYPE', 'HAS_ANNOTATION', 'OVERRIDES', 'THROWS',
    'SOURCE_FILE', 'AST_CHILD', 'CFG_NEXT', 'DATA_FLOW',
    'IMPORTS', 'EXPORTS', 'RENDERS', 'USES_HOOK', 'PROP_DEPENDENCY',
    'DECORATES', 'YIELDS',
    'EXTENSION_OF', 'DELEGATES_TO', 'SEALED_SUBTYPE', 'COMPANION_OF', 'SUSPENDS',
]

# Allowed Cypher clauses for read-only validation
_READ_ONLY_PATTERN = re.compile(
    r'^\s*(MATCH|RETURN|WITH|WHERE|ORDER|SKIP|LIMIT|UNWIND|OPTIONAL|CALL|UNION)\b',
    re.IGNORECASE
)
_WRITE_PATTERN = re.compile(
    r'\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER)\b',
    re.IGNORECASE
)


def _is_read_only(cypher: str) -> bool:
    """Validate that a Cypher query is read-only."""
    return bool(_READ_ONLY_PATTERN.match(cypher)) and not bool(_WRITE_PATTERN.search(cypher))


class KuzuBackend:
    def __init__(self, db_path: str):
        import kuzu
        self.db_path = os.path.abspath(db_path)
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"KuzuDB database not found at {self.db_path}")
        self.db = kuzu.Database(self.db_path)
        self.conn = kuzu.Connection(self.db)

    def execute(self, cypher: str) -> list[dict]:
        result = self.conn.execute(cypher)
        columns = result.get_column_names()
        rows = []
        while result.has_next():
            values = result.get_next()
            rows.append(dict(zip(columns, values)))
        return rows

    def close(self):
        pass


class Neo4jBackend:
    def __init__(self, uri: str, username: str, password: str, database: str = 'neo4j'):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database

    def execute(self, cypher: str) -> list[dict]:
        with self.driver.session(database=self.database) as session:
            result = session.run(cypher)
            return [dict(record) for record in result]

    def close(self):
        self.driver.close()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _format_rows(rows: list[dict], limit: int = 100) -> str:
    """Format query result rows as readable text."""
    if not rows:
        return "No results."
    truncated = rows[:limit]
    lines = [json.dumps(row, default=str) for row in truncated]
    text = "\n".join(lines)
    if len(rows) > limit:
        text += f"\n... ({len(rows) - limit} more rows truncated)"
    return text


# ── MCP Server ──────────────────────────────────────────────────────────────

def build_server(backend) -> FastMCP:
    mcp = FastMCP(
        "code-mem-graph",
        instructions=(
            "MCP server for querying a Code Property Graph (CPG). "
            "The graph contains parsed source code: packages, classes, methods, "
            "functions, fields, parameters, statements, and relationships like "
            "CALLS, CONTAINS, EXTENDS, IMPLEMENTS, CFG_NEXT, DATA_FLOW, etc. "
            "Use the tools below to explore the codebase graph."
        ),
    )

    @mcp.tool()
    def query(cypher: str) -> str:
        """Run a read-only Cypher query against the code graph and return results.

        Examples:
          MATCH (c:Class) RETURN c.name, c.qualifiedName LIMIT 10
          MATCH (a)-[r:CALLS]->(b) RETURN a.qualifiedName, b.qualifiedName LIMIT 20
          MATCH (m:Method {name: 'parse'}) RETURN m.qualifiedName, m.lineNumber
        """
        if not _is_read_only(cypher):
            return "Error: only read-only queries (MATCH/RETURN) are allowed."
        try:
            rows = backend.execute(cypher)
            return _format_rows(rows)
        except Exception as e:
            return f"Query error: {e}"

    @mcp.tool()
    def get_schema() -> str:
        """Get the graph schema: all node types, relationship types, and node properties."""
        schema = {
            "node_types": NODE_TYPES,
            "node_properties": [
                "id", "name", "qualifiedName", "visibility",
                "isAbstract", "isStatic", "isFinal", "returnType",
                "lineNumber", "endLineNumber", "type", "external",
                "path", "statementType", "code",
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
            },
        }
        return json.dumps(schema, indent=2)

    @mcp.tool()
    def summary() -> str:
        """Get a summary of the code graph: node counts per type and total relationships."""
        lines = []
        total_nodes = 0
        for t in NODE_TYPES:
            try:
                rows = backend.execute(f"MATCH (n:{t}) RETURN count(n) AS c")
                count = rows[0]['c'] if rows else 0
                if count > 0:
                    lines.append(f"  {t}: {count}")
                    total_nodes += count
            except Exception:
                pass
        total_rels = 0
        for r in REL_TYPES:
            try:
                rows = backend.execute(f"MATCH ()-[r:{r}]->() RETURN count(r) AS c")
                count = rows[0]['c'] if rows else 0
                if count > 0:
                    lines.append(f"  {r}: {count}")
                    total_rels += count
            except Exception:
                pass
        header = f"Nodes: {total_nodes}  |  Relationships: {total_rels}\n"
        return header + "\n".join(lines) if lines else "Graph is empty."

    @mcp.tool()
    def search_nodes(name_pattern: str, node_type: str = "") -> str:
        """Search for nodes by name pattern (supports * wildcards) and optional type.

        Args:
            name_pattern: Name pattern to match. Use * as wildcard (e.g. '*Service*', 'User*').
            node_type: Optional node type filter (e.g. 'Class', 'Method', 'Function').
        """
        like_pattern = name_pattern.replace('*', '%')
        label = node_type if node_type in NODE_TYPES else None

        if label:
            cypher = (
                f"MATCH (n:{label}) WHERE n.name =~ '(?i).*{re.escape(name_pattern).replace(chr(92) + '*', '.*')}.*' "
                f"RETURN n.name AS name, n.qualifiedName AS qualifiedName, "
                f"n.lineNumber AS line, n.path AS path LIMIT 50"
            )
        else:
            # Search across common types
            parts = []
            for t in ['Package', 'Class', 'Interface', 'Method', 'Function',
                       'Module', 'Component', 'Field', 'Constructor']:
                parts.append(
                    f"MATCH (n:{t}) WHERE n.name =~ '(?i).*{re.escape(name_pattern).replace(chr(92) + '*', '.*')}.*' "
                    f"RETURN n.name AS name, n.qualifiedName AS qualifiedName, "
                    f"'{t}' AS type, n.lineNumber AS line, n.path AS path"
                )
            cypher = " UNION ALL ".join(parts) + " LIMIT 50"

        try:
            rows = backend.execute(cypher)
            return _format_rows(rows)
        except Exception as e:
            return f"Search error: {e}"

    @mcp.tool()
    def get_callers(method_name: str) -> str:
        """Find all callers of a given method or function.

        Args:
            method_name: The name or qualified name of the method/function.
        """
        cypher = (
            f"MATCH (caller)-[r:CALLS]->(callee) "
            f"WHERE callee.name = '{_cypher_escape(method_name)}' "
            f"   OR callee.qualifiedName = '{_cypher_escape(method_name)}' "
            f"RETURN caller.qualifiedName AS caller, callee.qualifiedName AS callee, "
            f"r.lineNumber AS line LIMIT 50"
        )
        try:
            rows = backend.execute(cypher)
            return _format_rows(rows) if rows else f"No callers found for '{method_name}'."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_callees(method_name: str) -> str:
        """Find all methods/functions called by a given method or function.

        Args:
            method_name: The name or qualified name of the method/function.
        """
        cypher = (
            f"MATCH (caller)-[r:CALLS]->(callee) "
            f"WHERE caller.name = '{_cypher_escape(method_name)}' "
            f"   OR caller.qualifiedName = '{_cypher_escape(method_name)}' "
            f"RETURN caller.qualifiedName AS caller, callee.qualifiedName AS callee, "
            f"r.lineNumber AS line LIMIT 50"
        )
        try:
            rows = backend.execute(cypher)
            return _format_rows(rows) if rows else f"No callees found for '{method_name}'."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def get_class_hierarchy(class_name: str) -> str:
        """Get the inheritance hierarchy (EXTENDS / IMPLEMENTS) for a class or interface.

        Args:
            class_name: The name or qualified name of the class/interface.
        """
        lines = []
        # Parents (what this class extends/implements)
        for rel in ['EXTENDS', 'IMPLEMENTS']:
            cypher = (
                f"MATCH (child)-[:{rel}]->(parent) "
                f"WHERE child.name = '{_cypher_escape(class_name)}' "
                f"   OR child.qualifiedName = '{_cypher_escape(class_name)}' "
                f"RETURN child.qualifiedName AS child, parent.qualifiedName AS parent, "
                f"'{rel}' AS rel"
            )
            try:
                rows = backend.execute(cypher)
                for r in rows:
                    lines.append(f"{r['child']} --{r['rel']}--> {r['parent']}")
            except Exception:
                pass

        # Children (what extends/implements this class)
        for rel in ['EXTENDS', 'IMPLEMENTS']:
            cypher = (
                f"MATCH (child)-[:{rel}]->(parent) "
                f"WHERE parent.name = '{_cypher_escape(class_name)}' "
                f"   OR parent.qualifiedName = '{_cypher_escape(class_name)}' "
                f"RETURN child.qualifiedName AS child, parent.qualifiedName AS parent, "
                f"'{rel}' AS rel"
            )
            try:
                rows = backend.execute(cypher)
                for r in rows:
                    lines.append(f"{r['child']} --{r['rel']}--> {r['parent']}")
            except Exception:
                pass

        return "\n".join(lines) if lines else f"No hierarchy found for '{class_name}'."

    @mcp.tool()
    def get_control_flow(method_name: str) -> str:
        """Get the control-flow graph (CFG) edges for a method or function.

        Args:
            method_name: The name or qualified name of the method/function.
        """
        cypher = (
            f"MATCH (m)-[:CFG_NEXT*1..1]->(s:Statement) "
            f"WHERE m.name = '{_cypher_escape(method_name)}' "
            f"   OR m.qualifiedName = '{_cypher_escape(method_name)}' "
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

    @mcp.tool()
    def get_data_flow(method_name: str) -> str:
        """Get data-flow edges for a method or function, showing how variables propagate.

        Args:
            method_name: The name or qualified name of the method/function.
        """
        cypher = (
            f"MATCH (m)-[:CFG_NEXT*1..1]->(s:Statement) "
            f"WHERE m.name = '{_cypher_escape(method_name)}' "
            f"   OR m.qualifiedName = '{_cypher_escape(method_name)}' "
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

    return mcp


def _cypher_escape(s: str) -> str:
    """Escape a string for safe inclusion in a Cypher literal."""
    return s.replace('\\', '\\\\').replace("'", "\\'")


# ── CLI entry point ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='MCP server for code-mem-graph Code Property Graph'
    )
    parser.add_argument('--backend', choices=['kuzu', 'neo4j'], default='kuzu',
                        help='Graph backend (default: kuzu)')
    parser.add_argument('--db-path', required=True,
                        help='KuzuDB database file path (required)')
    parser.add_argument('--neo4j-uri', default='bolt://localhost:7687')
    parser.add_argument('--neo4j-user', default='neo4j')
    parser.add_argument('--neo4j-password', default='')
    parser.add_argument('--neo4j-database', default='neo4j')

    args = parser.parse_args()

    if args.backend == 'kuzu':
        if not os.path.isfile(args.db_path):
            print(f"Error: --db-path must be a KuzuDB database file (not a directory): {args.db_path}", file=sys.stderr)
            sys.exit(1)
        backend = KuzuBackend(args.db_path)
        print(f"MCP server: KuzuDB at {backend.db_path}", file=sys.stderr)
    else:
        backend = Neo4jBackend(
            args.neo4j_uri, args.neo4j_user,
            args.neo4j_password, args.neo4j_database
        )
        print(f"MCP server: Neo4j at {args.neo4j_uri}", file=sys.stderr)

    server = build_server(backend)
    server.run(transport='stdio')


if __name__ == '__main__':
    main()
