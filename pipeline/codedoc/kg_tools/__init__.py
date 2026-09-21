"""Knowledge-graph query toolkit for code reverse-engineering.

Provides ``create_graph_backend``, ``ToolRegistry``, and
``ReverseEngineerToolkit`` with 30+ tools for querying code property graphs.

Language-agnostic: works with Java, Kotlin, JavaScript/TypeScript, and
Python code graphs produced by any cmg-* indexer, and with graphs produced
by the experimental treesitter-indexer prototype (LadybugDB-backed).

Usage::

    from codedoc.kg_tools import create_graph_backend, ReverseEngineerToolkit

    backend = create_graph_backend("./kuzu_db/my-project-db")
    toolkit = ReverseEngineerToolkit(backend)
    tools   = toolkit.openai_tool_definitions()
    result  = toolkit.call("summary")
"""

from codedoc.kg_tools.backends import (
    KuzuBackend, LadybugBackend, Neo4jBackend, create_graph_backend, sniff_backend_kind,
)
from codedoc.kg_tools.registry import ToolDef, ToolRegistry
from codedoc.kg_tools.toolkit import ReverseEngineerToolkit

__all__ = [
    "KuzuBackend", "LadybugBackend", "Neo4jBackend", "create_graph_backend", "sniff_backend_kind",
    "ToolDef", "ToolRegistry", "ReverseEngineerToolkit",
]
