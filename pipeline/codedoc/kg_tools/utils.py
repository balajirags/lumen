"""Shared constants and utility functions for kg_tools."""

import json

# ── Constants ───────────────────────────────────────────────────────────────

NODE_TYPES = [
    "Package", "Class", "Interface", "Enum", "Record", "AnnotationType",
    "Method", "Constructor", "Field", "Parameter", "File", "Statement",
    "Module", "Function", "ArrowFunction", "Component", "Hook", "JSXElement",
    "Decorator", "Generator", "AsyncFunction", "Comprehension",
    "DataClass", "SealedClass", "SealedInterface", "ObjectDecl",
    "CompanionObject", "ExtensionFunction", "SuspendFunction",
    "Property", "Lambda", "InitBlock", "TypeAlias",
    # Post-processing abstractions
    "Workflow",   # end-to-end execution trace (HTTP entry → repository/event terminal)
    "Domain",     # functional cluster of cohesive classes/modules
    # Config (experimental treesitter-indexer only -- see treesitter-indexer/config/)
    "ConfigFile",      # a YAML/JSON/.properties file under src/main/resources
    "ConfigProperty",  # a flattened dotted-key config entry (e.g. spring.datasource.url)
]

REL_TYPES = [
    "CONTAINS", "EXTENDS", "IMPLEMENTS", "CALLS", "RETURNS",
    "HAS_PARAMETER", "OF_TYPE", "HAS_ANNOTATION", "OVERRIDES", "THROWS",
    "SOURCE_FILE", "AST_CHILD", "CFG_NEXT", "DATA_FLOW",
    "IMPORTS", "EXPORTS", "RENDERS", "USES_HOOK", "PROP_DEPENDENCY",
    "DECORATES", "YIELDS",
    "EXTENSION_OF", "DELEGATES_TO", "SEALED_SUBTYPE", "COMPANION_OF", "SUSPENDS",
    # Post-processing abstractions
    "WORKFLOW_STEP",  # (Method|Component|Function)-[:WORKFLOW_STEP {step}]->(Workflow)
    "IN_DOMAIN",      # (Class|Interface|Component|Module|...)-[:IN_DOMAIN]->(Domain)
    # Config (experimental treesitter-indexer only)
    "REFERENCES_CONFIG",  # (Field|Parameter|Method)-[:REFERENCES_CONFIG]->(ConfigProperty)
]

# CALLS edge properties:
#   lineNumber INT64   — source line of the call site
#   confidence DOUBLE  — 0.95=same-file, 0.90=import-resolved, 0.50=global/inferred
#   reason     STRING  — "same-file" | "import-resolved" | "global"
# Use confidence >= 0.9 to filter to reliable calls only.
#
# HAS_ANNOTATION edge properties:
#   value STRING — the primary annotation attribute (e.g. "/api/v1/inventory" for @RequestMapping)

def _cypher_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _format_rows(rows: list[dict], limit: int = 150, max_chars: int = 8000) -> str:
    """Format query result rows as compact text. Caps output size for token efficiency."""
    if not rows:
        return "No results."
    lines: list[str] = []
    total_chars = 0
    for r in rows[:limit]:
        # Strip None values for compactness
        clean = {k: v for k, v in r.items() if v is not None}
        line = json.dumps(clean, default=str)
        total_chars += len(line) + 1
        if total_chars > max_chars:
            lines.append(f"... ({len(rows) - len(lines)} more rows truncated for token budget)")
            break
        lines.append(line)
    else:
        if len(rows) > limit:
            lines.append(f"... ({len(rows) - limit} more rows truncated)")
    return "\n".join(lines)
