#!/usr/bin/env python3
"""
Python source code parser using the ast module.
Builds a Code Property Graph (AST + CFG + data flow) and writes to KuzuDB, Neo4j, or JSON.

Uses python-graphs (Google Research) for control-flow analysis and variable
access tracking, supplemented with a statement-level CFG walker that mirrors
the Java CpgParser for full CPG edge coverage.

Usage: python parse.py <directory> [options]
  --backend kuzu|neo4j|json   Graph backend (default: kuzu)
  --db-path <path>            KuzuDB database path (default: <bin-dir>/kuzu_db/<repo-name>-db)
  --clear                     Clear existing graph before writing
  --neo4j-uri <uri>           Neo4j URI (default: bolt://localhost:7687)
  --neo4j-user <user>         Neo4j username (default: neo4j)
  --neo4j-password <pass>     Neo4j password
  --neo4j-database <db>       Neo4j database (default: neo4j)
"""

import ast
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Any, Optional


class CodeGraphBuilder:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.relationships: List[Dict[str, Any]] = []
        self.internal_modules: Set[str] = set()

    def add_node(self, node_id: str, node_type: str, name: str, 
                 qualified_name: str, properties: Dict[str, Any] = None):
        if node_id not in self.nodes:
            self.nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "name": name,
                "qualifiedName": qualified_name,
                "properties": properties or {}
            }

    def add_relationship(self, source_id: str, target_id: str, 
                        rel_type: str, properties: Dict[str, Any] = None):
        self.relationships.append({
            "sourceId": source_id,
            "targetId": target_id,
            "type": rel_type,
            "properties": properties or {}
        })

    def has_node(self, node_id: str) -> bool:
        return node_id in self.nodes

    def to_json(self) -> Dict[str, Any]:
        return {
            "nodes": list(self.nodes.values()),
            "relationships": self.relationships
        }


class PythonVisitor(ast.NodeVisitor):
    """AST visitor that builds a CodeGraph from Python source."""
    
    def __init__(self, graph: CodeGraphBuilder, module_name: str, file_path: str):
        self.graph = graph
        self.module_name = module_name
        self.file_path = file_path
        self.module_id = f"module:{module_name}"
        self.current_class: Optional[str] = None
        self.current_function: Optional[str] = None
        self.imports: Dict[str, str] = {}  # alias -> module
        self.from_imports: Dict[str, tuple] = {}  # name -> (module, original_name)
        
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            name = alias.asname or alias.name
            self.imports[name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            name = alias.asname or alias.name
            original = alias.name
            self.from_imports[name] = (module, original)
            
            # Only create IMPORTS relationship for relative imports (within project)
            if node.level > 0:  # Relative import
                target_module = self._resolve_relative_import(module, node.level)
                self.graph.add_relationship(
                    self.module_id, 
                    f"module:{target_module}", 
                    "IMPORTS",
                    {"importedName": original, "localName": name}
                )
        self.generic_visit(node)

    def _resolve_relative_import(self, module: str, level: int) -> str:
        """Resolve relative import to absolute module name."""
        parts = self.module_name.split(".")
        if level > len(parts):
            return module or ""
        base = ".".join(parts[:-level]) if level <= len(parts) else ""
        if module:
            return f"{base}.{module}" if base else module
        return base

    def visit_ClassDef(self, node: ast.ClassDef):
        class_name = node.name
        class_id = f"class:{self.module_name}.{class_name}"
        
        # Check for decorators
        decorators = [self._get_decorator_name(d) for d in node.decorator_list]
        
        self.graph.add_node(
            class_id, "CLASS", class_name,
            f"{self.module_name}.{class_name}",
            {
                "lineNumber": node.lineno,
                "decorators": decorators,
                "language": "python"
            }
        )
        
        self.graph.add_relationship(self.module_id, class_id, "CONTAINS")
        
        # Handle decorators
        for dec in node.decorator_list:
            dec_name = self._get_decorator_name(dec)
            dec_id = f"decorator:{dec_name}"
            self.graph.add_node(dec_id, "DECORATOR", dec_name, dec_name)
            self.graph.add_relationship(dec_id, class_id, "DECORATES")
        
        # Handle base classes
        for base in node.bases:
            base_name = self._get_name(base)
            if base_name:
                base_id = f"class:{base_name}"
                self.graph.add_node(base_id, "CLASS", base_name, base_name)
                self.graph.add_relationship(class_id, base_id, "EXTENDS")
        
        # Extract class-level field declarations for ER diagram support.
        # Handles: annotated class variables (dataclass, Pydantic, plain typed attrs)
        # and ORM-style column assignments (SQLAlchemy, Django models).
        for item in node.body:
            field_name = None
            type_name = None

            # Annotated assignment: `name: str` or `name: str = default`
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                field_name = item.target.id
                type_name = self._get_name(item.annotation) or ast.unparse(item.annotation) if hasattr(ast, 'unparse') else None

            # Untyped assignment that looks like an ORM column:
            # `id = Column(...)`, `name = models.CharField(...)`, `email = fields.CharField(...)`
            elif (isinstance(item, ast.Assign)
                  and len(item.targets) == 1
                  and isinstance(item.targets[0], ast.Name)
                  and isinstance(item.value, ast.Call)):
                call_func = item.value.func
                call_name = self._get_name(call_func) or ''
                _ORM_CALLS = {'Column', 'Field', 'CharField', 'IntegerField',
                              'ForeignKey', 'relationship', 'mapped_column',
                              'TextField', 'BooleanField', 'DateTimeField',
                              'DecimalField', 'FloatField', 'EmailField'}
                func_leaf = call_name.split('.')[-1]
                if func_leaf in _ORM_CALLS:
                    field_name = item.targets[0].id
                    # Try to extract type from first positional arg (e.g. Column(Integer))
                    if item.value.args:
                        type_name = self._get_name(item.value.args[0])
                    else:
                        type_name = func_leaf  # fallback: use the column type name

            if field_name and not field_name.startswith('_'):
                field_id = f"field:{self.module_name}.{class_name}.{field_name}"
                self.graph.add_node(
                    field_id, "FIELD", field_name,
                    f"{self.module_name}.{class_name}.{field_name}",
                    {
                        "lineNumber": getattr(item, 'lineno', -1),
                        "type": type_name,
                        "visibility": "public",
                        "language": "python",
                        "external": False,
                    }
                )
                self.graph.add_relationship(class_id, field_id, "CONTAINS")
                # OF_TYPE edge when the type looks like a user-defined class (PascalCase)
                if type_name and type_name[:1].isupper():
                    type_id = f"class:{type_name}"
                    if not self.graph.has_node(type_id):
                        self.graph.add_node(type_id, "CLASS", type_name, type_name,
                                            {"language": "python", "external": True})
                    self.graph.add_relationship(field_id, type_id, "OF_TYPE")

        # Visit class body
        old_class = self.current_class
        self.current_class = class_id
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._visit_function(node, is_async=False)
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_function(node, is_async=True)

    def _visit_function(self, node, is_async: bool):
        func_name = node.name
        
        if self.current_class:
            # Method within a class
            class_name = self.current_class.replace("class:", "")
            func_id = f"method:{class_name}.{func_name}"
            qualified_name = f"{class_name}.{func_name}"
            
            # Determine if it's a constructor
            if func_name == "__init__":
                node_type = "CONSTRUCTOR"
            else:
                node_type = "ASYNC_FUNCTION" if is_async else "METHOD"
            
            self.graph.add_node(
                func_id, node_type, func_name, qualified_name,
                {
                    "lineNumber": node.lineno,
                    "isAsync": is_async,
                    "paramCount": len(node.args.args),
                    "isPrivate": func_name.startswith("_"),
                    "isProperty": any(self._get_decorator_name(d) == "property"
                                     for d in node.decorator_list),
                    "language": "python"
                }
            )
            
            self.graph.add_relationship(self.current_class, func_id, "CONTAINS")
        else:
            # Module-level function
            func_id = f"function:{self.module_name}.{func_name}"
            qualified_name = f"{self.module_name}.{func_name}"
            
            # Check for generator
            is_generator = any(isinstance(n, (ast.Yield, ast.YieldFrom)) 
                              for n in ast.walk(node))
            
            if is_generator:
                node_type = "GENERATOR"
            elif is_async:
                node_type = "ASYNC_FUNCTION"
            else:
                node_type = "FUNCTION"
            
            self.graph.add_node(
                func_id, node_type, func_name, qualified_name,
                {
                    "lineNumber": node.lineno,
                    "isAsync": is_async,
                    "isGenerator": is_generator,
                    "paramCount": len(node.args.args),
                    "language": "python"
                }
            )
            
            self.graph.add_relationship(self.module_id, func_id, "CONTAINS")
        
        # Handle decorators
        for dec in node.decorator_list:
            dec_name = self._get_decorator_name(dec)
            dec_id = f"decorator:{dec_name}"
            self.graph.add_node(dec_id, "DECORATOR", dec_name, dec_name)
            self.graph.add_relationship(dec_id, func_id, "DECORATES")
        
        # Visit function body
        old_function = self.current_function
        self.current_function = func_id
        self.generic_visit(node)
        self.current_function = old_function

    def visit_Call(self, node: ast.Call):
        caller = self.current_function or self.current_class or self.module_id
        callee_name = self._get_name(node.func)
        
        if not callee_name:
            self.generic_visit(node)
            return
        
        # Check if it's an imported function
        if callee_name in self.from_imports:
            module, original = self.from_imports[callee_name]
            # Only track internal module calls
            if self._is_internal_module(module):
                target_id = f"function:{module}.{original}"
                self.graph.add_relationship(
                    caller, target_id, "CALLS",
                    {"lineNumber": node.lineno, "resolved": True}
                )
        elif callee_name in self.imports:
            # Module-level import call like `os.path.join()`
            pass  # Skip external module calls
        elif "." not in callee_name:
            # Local function call
            target_id = f"function:{self.module_name}.{callee_name}"
            self.graph.add_relationship(
                caller, target_id, "CALLS",
                {"lineNumber": node.lineno, "resolved": True}
            )
        elif "." in callee_name:
            # Method call like `self.method()` or `obj.method()`
            parts = callee_name.split(".")
            if parts[0] == "self" and self.current_class:
                class_name = self.current_class.replace("class:", "")
                method_name = parts[-1]
                target_id = f"method:{class_name}.{method_name}"
                self.graph.add_relationship(
                    caller, target_id, "CALLS",
                    {"lineNumber": node.lineno, "resolved": True}
                )
        
        self.generic_visit(node)

    def _is_internal_module(self, module: str) -> bool:
        """Check if a module is internal (within the project)."""
        return module in self.graph.internal_modules or module.split(".")[0] in self.graph.internal_modules

    def _get_name(self, node) -> Optional[str]:
        """Extract name from various AST node types."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._get_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        elif isinstance(node, ast.Subscript):
            return self._get_name(node.value)
        return None

    def _get_decorator_name(self, node) -> str:
        """Extract decorator name."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return self._get_name(node) or "unknown"
        elif isinstance(node, ast.Call):
            return self._get_name(node.func) or "unknown"
        return "unknown"


# ─── CPG Enhancer (CFG + Data Flow) ────────────────────────────────────────


def _classify_stmt(stmt) -> str:
    """Classify a Python AST statement into a CPG statement type."""
    type_map = {
        ast.If: "IF",
        ast.For: "FOR",
        ast.AsyncFor: "ASYNC_FOR",
        ast.While: "WHILE",
        ast.Return: "RETURN",
        ast.Raise: "RAISE",
        ast.Try: "TRY",
        ast.With: "WITH",
        ast.AsyncWith: "ASYNC_WITH",
        ast.Assert: "ASSERT",
        ast.Delete: "DELETE",
        ast.Import: "IMPORT",
        ast.ImportFrom: "IMPORT",
        ast.Assign: "ASSIGNMENT",
        ast.AugAssign: "AUG_ASSIGNMENT",
        ast.AnnAssign: "ANNOTATED_ASSIGNMENT",
        ast.Pass: "PASS",
        ast.Break: "BREAK",
        ast.Continue: "CONTINUE",
        ast.Global: "GLOBAL",
        ast.Nonlocal: "NONLOCAL",
        ast.Expr: "EXPRESSION",
    }
    # Python 3.10+ match statement
    if hasattr(ast, "Match") and isinstance(stmt, ast.Match):
        return "MATCH"
    if hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar):
        return "TRY"
    return type_map.get(type(stmt), "OTHER")


def _stmt_code(stmt) -> str:
    """Get a concise code representation of a statement (max 200 chars)."""
    try:
        code = ast.unparse(stmt)
    except Exception:
        code = type(stmt).__name__
    if len(code) > 200:
        code = code[:200] + "..."
    return code


def _collect_reads(stmt) -> Set[str]:
    """Collect all variable reads (Name with Load context) in a statement."""
    reads = set()
    for node in ast.walk(stmt):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            reads.add(node.id)
    return reads


def _collect_writes(stmt) -> Set[str]:
    """Collect all variable writes in a statement."""
    writes = set()
    for node in ast.walk(stmt):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            writes.add(node.id)
    return writes


class CpgEnhancer:
    """
    Second-pass enhancer that adds CPG edges (AST_CHILD, CFG_NEXT, DATA_FLOW)
    to an existing structural graph. Mirrors CpgParser.java.
    """

    def __init__(self, graph: CodeGraphBuilder):
        self.graph = graph
        self.stmt_count = 0
        self.cfg_edge_count = 0
        self.data_flow_count = 0

    def enhance_file(self, tree: ast.AST, module_name: str, relative_path: str):
        """Add CPG edges for all functions/methods in a parsed file."""
        file_id = f"file:{relative_path}"

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._process_function(node, module_name, file_id)

    def _process_function(self, func_node, module_name: str, file_id: str):
        """Process a single function/method body for CPG edges."""
        func_name = func_node.name

        # Find the containing class (if any) by checking parent info
        # We stored the function id during structural pass — find it
        parent_class = self._find_parent_class(func_node)

        if parent_class:
            class_qname = f"{module_name}.{parent_class}"
            if func_name == "__init__":
                func_id = f"method:{class_qname}.{func_name}"
            else:
                func_id = f"method:{class_qname}.{func_name}"
        else:
            func_id = f"function:{module_name}.{func_name}"

        if not self.graph.has_node(func_id):
            return

        # Build a unique prefix for statement IDs
        qualified = func_id.replace("method:", "").replace("function:", "")
        counter = [0]
        var_defs: Dict[str, str] = {}

        self._process_statements(
            func_node.body, func_id, qualified, counter, var_defs, [func_id]
        )

    def _find_parent_class(self, node) -> Optional[str]:
        """Walk up to find containing ClassDef name via _parent attribute."""
        current = getattr(node, '_parent', None)
        while current is not None:
            if isinstance(current, ast.ClassDef):
                return current.name
            current = getattr(current, '_parent', None)
        return None

    def _process_statements(
        self,
        stmts: List[ast.stmt],
        parent_id: str,
        qualified_prefix: str,
        counter: List[int],
        var_defs: Dict[str, str],
        cfg_predecessors: List[str],
    ) -> List[str]:
        """
        Process a list of statements: create STATEMENT nodes, AST_CHILD,
        CFG_NEXT, and DATA_FLOW edges. Returns exit statement IDs.
        """
        current_preds = list(cfg_predecessors)

        for stmt in stmts:
            stmt_type = _classify_stmt(stmt)
            code = _stmt_code(stmt)
            line = getattr(stmt, 'lineno', -1)
            end_line = getattr(stmt, 'end_lineno', line)

            stmt_id = f"stmt:{qualified_prefix}:S{counter[0]}"
            counter[0] += 1

            # STATEMENT node
            self.graph.add_node(
                stmt_id, "STATEMENT", stmt_type,
                f"{qualified_prefix}:S{counter[0] - 1}",
                {
                    "statementType": stmt_type,
                    "code": code,
                    "lineNumber": line,
                    "endLineNumber": end_line,
                }
            )
            self.stmt_count += 1

            # AST_CHILD: parent → statement
            self.graph.add_relationship(
                parent_id, stmt_id, "AST_CHILD",
                {"ast_order": counter[0] - 1}
            )

            # CFG_NEXT: predecessors → this statement
            for pred in current_preds:
                self.graph.add_relationship(pred, stmt_id, "CFG_NEXT")
                self.cfg_edge_count += 1

            # DATA_FLOW: variable reads link back to last definition
            self._process_data_flow(stmt, stmt_id, var_defs)

            # Handle compound statements for CFG branching
            if isinstance(stmt, ast.If):
                current_preds = self._process_if(
                    stmt, stmt_id, qualified_prefix, counter, var_defs
                )
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                current_preds = self._process_loop(
                    stmt.body, stmt_id, qualified_prefix, counter, var_defs
                )
            elif isinstance(stmt, ast.While):
                current_preds = self._process_loop(
                    stmt.body, stmt_id, qualified_prefix, counter, var_defs
                )
            elif isinstance(stmt, (ast.Try,)):
                current_preds = self._process_try(
                    stmt, stmt_id, qualified_prefix, counter, var_defs
                )
            elif hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar):
                current_preds = self._process_try(
                    stmt, stmt_id, qualified_prefix, counter, var_defs
                )
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                current_preds = self._process_statements(
                    stmt.body, stmt_id, qualified_prefix, counter, var_defs,
                    [stmt_id]
                )
            elif hasattr(ast, "Match") and isinstance(stmt, ast.Match):
                current_preds = self._process_match(
                    stmt, stmt_id, qualified_prefix, counter, var_defs
                )
            elif isinstance(stmt, (ast.Return, ast.Raise)):
                current_preds = []  # terminal — no outgoing CFG
            elif isinstance(stmt, (ast.Break, ast.Continue)):
                current_preds = []
            else:
                current_preds = [stmt_id]

        return current_preds

    def _process_if(self, if_stmt: ast.If, stmt_id: str,
                    prefix: str, counter: List[int],
                    var_defs: Dict[str, str]) -> List[str]:
        exits = []
        # Then branch
        exits.extend(self._process_statements(
            if_stmt.body, stmt_id, prefix, counter, var_defs, [stmt_id]
        ))
        # Else branch
        if if_stmt.orelse:
            exits.extend(self._process_statements(
                if_stmt.orelse, stmt_id, prefix, counter, var_defs, [stmt_id]
            ))
        else:
            exits.append(stmt_id)
        return exits

    def _process_loop(self, body: List[ast.stmt], loop_stmt_id: str,
                      prefix: str, counter: List[int],
                      var_defs: Dict[str, str]) -> List[str]:
        body_exits = self._process_statements(
            body, loop_stmt_id, prefix, counter, var_defs, [loop_stmt_id]
        )
        # Back edge: body exits → loop head
        for exit_id in body_exits:
            self.graph.add_relationship(
                exit_id, loop_stmt_id, "CFG_NEXT", {"backEdge": True}
            )
            self.cfg_edge_count += 1
        return [loop_stmt_id]

    def _process_try(self, try_stmt, stmt_id: str,
                     prefix: str, counter: List[int],
                     var_defs: Dict[str, str]) -> List[str]:
        exits = []
        # Try body
        exits.extend(self._process_statements(
            try_stmt.body, stmt_id, prefix, counter, var_defs, [stmt_id]
        ))
        # Handlers (except clauses)
        for handler in try_stmt.handlers:
            exits.extend(self._process_statements(
                handler.body, stmt_id, prefix, counter, var_defs, [stmt_id]
            ))
        # Else clause
        if try_stmt.orelse:
            exits.extend(self._process_statements(
                try_stmt.orelse, stmt_id, prefix, counter, var_defs, [stmt_id]
            ))
        # Finally clause
        if try_stmt.finalbody:
            finally_exits = self._process_statements(
                try_stmt.finalbody, stmt_id, prefix, counter, var_defs, [stmt_id]
            )
            exits = finally_exits
        return exits

    def _process_match(self, match_stmt, stmt_id: str,
                       prefix: str, counter: List[int],
                       var_defs: Dict[str, str]) -> List[str]:
        exits = []
        for case in match_stmt.cases:
            exits.extend(self._process_statements(
                case.body, stmt_id, prefix, counter, var_defs, [stmt_id]
            ))
        if not exits:
            exits.append(stmt_id)
        return exits

    def _process_data_flow(self, stmt, stmt_id: str, var_defs: Dict[str, str]):
        """Track variable reads/writes and create DATA_FLOW edges."""
        # Find reads — link to last definition
        reads = _collect_reads(stmt)
        for var_name in reads:
            def_stmt_id = var_defs.get(var_name)
            if def_stmt_id is not None:
                self.graph.add_relationship(
                    def_stmt_id, stmt_id, "DATA_FLOW", {"variable": var_name}
                )
                self.data_flow_count += 1

        # Track writes — update last definition
        writes = _collect_writes(stmt)
        for var_name in writes:
            var_defs[var_name] = stmt_id


def collect_python_files(root_dir: Path) -> List[Path]:
    """Recursively collect all Python files."""
    files = []
    skip_dirs = {"__pycache__", ".git", "venv", ".venv", "env", ".env", 
                 "node_modules", "build", "dist", ".tox", ".pytest_cache"}
    
    for path in root_dir.rglob("*.py"):
        # Skip files in excluded directories
        if any(skip in path.parts for skip in skip_dirs):
            continue
        files.append(path)
    
    return files


def path_to_module_name(file_path: Path, root_dir: Path) -> str:
    """Convert file path to Python module name."""
    relative = file_path.relative_to(root_dir)
    parts = list(relative.parts)
    # Remove .py extension
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    # Handle __init__.py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "root"


def parse_file(file_path: Path, root_dir: Path, graph: CodeGraphBuilder,
               cpg_enhancer: Optional['CpgEnhancer'] = None):
    """Parse a single Python file and add to the graph."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return

    module_name = path_to_module_name(file_path, root_dir)
    relative_path = str(file_path.relative_to(root_dir))
    
    # Add file node
    file_id = f"file:{relative_path}"
    graph.add_node(file_id, "FILE", file_path.name, relative_path, {
        "path": relative_path,
        "language": "Python"
    })
    
    # Add module node
    module_id = f"module:{module_name}"
    graph.add_node(module_id, "MODULE", module_name, module_name, {"language": "python"})
    graph.add_relationship(module_id, file_id, "SOURCE_FILE")
    graph.internal_modules.add(module_name)
    
    # Also add parent packages
    parts = module_name.split(".")
    for i in range(len(parts) - 1):
        pkg_name = ".".join(parts[:i+1])
        graph.internal_modules.add(pkg_name)

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        print(f"Syntax error in {file_path}: {e}", file=sys.stderr)
        return

    # Pass 1: structural graph (classes, functions, calls, etc.)
    visitor = PythonVisitor(graph, module_name, relative_path)
    visitor.visit(tree)

    # Pass 2: CPG edges (statements, CFG, data flow)
    if cpg_enhancer is not None:
        # Annotate parent references for CpgEnhancer._find_parent_class
        _set_parents(tree)
        cpg_enhancer.enhance_file(tree, module_name, relative_path)


def _set_parents(tree: ast.AST):
    """Annotate every AST node with a _parent attribute."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Python source code parser')
    parser.add_argument('directory', help='Root directory to parse')
    parser.add_argument('--backend', choices=['kuzu', 'neo4j', 'json'],
                        default='kuzu', help='Graph backend (default: kuzu)')
    parser.add_argument('--db-path', default=None,
                        help='KuzuDB database path (default: <bin-dir>/kuzu_db/<repo-name>-db)')
    parser.add_argument('--clear', action='store_true',
                        help='Clear existing graph before writing')
    parser.add_argument('--neo4j-uri', default='bolt://localhost:7687')
    parser.add_argument('--neo4j-user', default='neo4j')
    parser.add_argument('--neo4j-password', default='')
    parser.add_argument('--neo4j-database', default='neo4j')
    parser.add_argument('--repo-name', default=None,
                        help='Logical repository name (overrides directory name for DB naming)')
    
    args = parser.parse_args()
    root_dir = Path(args.directory).resolve()

    # --db-path must be a directory; always generate DB file name inside it
    repo_name = args.repo_name or root_dir.name
    if args.db_path is None:
        db_dir = Path.cwd() / 'kuzu_db'
    else:
        db_dir = Path(args.db_path)
    db_dir.mkdir(parents=True, exist_ok=True)
    args.db_path = str(db_dir / f'{repo_name}-db')

    if not root_dir.exists():
        print(f"Directory not found: {root_dir}", file=sys.stderr)
        sys.exit(1)

    graph = CodeGraphBuilder()
    files = collect_python_files(root_dir)
    
    print(f"Found {len(files)} Python files to parse", file=sys.stderr)

    # First pass: collect all internal module names
    for file_path in files:
        module_name = path_to_module_name(file_path, root_dir)
        graph.internal_modules.add(module_name)
        parts = module_name.split(".")
        for i in range(len(parts)):
            graph.internal_modules.add(".".join(parts[:i+1]))

    # Second pass: structural parse + CPG enhancement
    cpg = CpgEnhancer(graph)
    for file_path in files:
        parse_file(file_path, root_dir, graph, cpg_enhancer=cpg)

    print(f"CPG: {cpg.stmt_count} statement nodes, {cpg.cfg_edge_count} CFG edges, "
          f"{cpg.data_flow_count} data-flow edges", file=sys.stderr)

    # Filter relationships to only include internal targets
    filtered_rels = []
    for rel in graph.relationships:
        if rel["type"] in ("CALLS", "IMPORTS"):
            if rel["targetId"] in graph.nodes:
                filtered_rels.append(rel)
        else:
            filtered_rels.append(rel)
    graph.relationships = filtered_rels

    graph_json = graph.to_json()

    if args.backend == 'json':
        print(json.dumps(graph_json, indent=2))
        return

    # Write to graph database
    from store import create_store
    store = create_store(
        backend=args.backend,
        db_path=args.db_path,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        neo4j_database=args.neo4j_database,
    )
    try:
        store.init_schema()
        if args.clear:
            store.clear()
        store.save(graph_json)
        print(store.summary(), file=sys.stderr)
    finally:
        store.close()


if __name__ == "__main__":
    main()
