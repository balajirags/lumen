"""Shared constants, regex patterns, and utility functions for kg_tools."""

import json
import re

# ── Constants ───────────────────────────────────────────────────────────────

NODE_TYPES = [
    "Package", "Class", "Interface", "Enum", "Record", "AnnotationType",
    "Method", "Constructor", "Field", "Parameter", "File", "Statement",
    "Module", "Function", "ArrowFunction", "Component", "Hook", "JSXElement",
    "Decorator", "Generator", "AsyncFunction", "Comprehension",
    "DataClass", "SealedClass", "SealedInterface", "ObjectDecl",
    "CompanionObject", "ExtensionFunction", "SuspendFunction",
    "Property", "Lambda", "InitBlock", "TypeAlias",
]

REL_TYPES = [
    "CONTAINS", "EXTENDS", "IMPLEMENTS", "CALLS", "RETURNS",
    "HAS_PARAMETER", "OF_TYPE", "HAS_ANNOTATION", "OVERRIDES", "THROWS",
    "SOURCE_FILE", "AST_CHILD", "CFG_NEXT", "DATA_FLOW",
    "IMPORTS", "EXPORTS", "RENDERS", "USES_HOOK", "PROP_DEPENDENCY",
    "DECORATES", "YIELDS",
    "EXTENSION_OF", "DELEGATES_TO", "SEALED_SUBTYPE", "COMPANION_OF", "SUSPENDS",
]

_READ_ONLY_PATTERN = re.compile(
    r"^\s*(MATCH|RETURN|WITH|WHERE|ORDER|SKIP|LIMIT|UNWIND|OPTIONAL|CALL|UNION)\b",
    re.IGNORECASE,
)
_WRITE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER)\b",
    re.IGNORECASE,
)


def _is_read_only(cypher: str) -> bool:
    return bool(_READ_ONLY_PATTERN.match(cypher)) and not bool(_WRITE_PATTERN.search(cypher))


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
