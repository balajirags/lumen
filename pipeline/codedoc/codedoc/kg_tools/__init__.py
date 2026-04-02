"""Knowledge-graph query toolkit for code reverse-engineering.

Provides ``KuzuBackend``, ``ToolRegistry``, and ``ReverseEngineerToolkit``
with 30+ tools for querying code property graphs stored in KuzuDB.

Language-agnostic: works with Java, Kotlin, JavaScript/TypeScript, and
Python code graphs produced by any cmg-* indexer.

Usage::

    from codedoc.kg_tools import KuzuBackend, ReverseEngineerToolkit

    backend = KuzuBackend("./kuzu_db/my-project-db")
    toolkit = ReverseEngineerToolkit(backend)
    tools   = toolkit.openai_tool_definitions()
    result  = toolkit.call("summary")
"""

from codedoc.kg_tools.backends import KuzuBackend, Neo4jBackend
from codedoc.kg_tools.registry import ToolDef, ToolRegistry
from codedoc.kg_tools.toolkit import ReverseEngineerToolkit

__all__ = ["KuzuBackend", "Neo4jBackend", "ToolDef", "ToolRegistry", "ReverseEngineerToolkit"]
