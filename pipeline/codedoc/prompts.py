"""Shared prompt constants used across agent stages."""

GRAPH_CONVENTIONS_BASE = """
---
## Graph conventions — read before writing any Cypher

- **CONTAINS direction is always PARENT -[:CONTAINS]-> CHILD.**
  e.g. `(pkg:Package)-[:CONTAINS]->(cls:Class)` — never the reverse.
- **Package has NO `parent` property.** Use CONTAINS relationships to walk
  the package tree.
- **Verify unfamiliar property names** with `get_schema` first.
- **Prefer `qualifiedName`** over `name` for precise matching.
- **CONTAINS nesting**: File→Class→Method/Field. Package→Class also valid.

## Efficiency rules

- **Batch independent tool calls**: emit ALL tool calls in a single turn.
- **Do not repeat queries**: reuse results from conversation history.
- **Use `search_nodes` before `query`** for name lookups.
"""
