"""Deterministic C4 diagram rendering utilities."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from codedoc.llm import ToolDefinition, ToolParam


def _write_artifact(output_root: str, filename: str, content: str) -> str:
    """Persist a documentation artifact to disk."""
    clean = filename
    for prefix in (output_root, str(Path(output_root))):
        if clean.startswith(prefix + "/"):
            clean = clean[len(prefix) + 1:]
        elif clean.startswith(prefix):
            clean = clean[len(prefix):]
    clean = clean.lstrip("/")
    dest = Path(output_root) / clean
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return f"written: {dest}"


def _slug_id(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "node"


def _node_id(item: dict[str, Any]) -> str:
    return _slug_id(str(item.get("id") or item.get("name") or ""))


def _validate_c4_spec(spec: dict[str, Any]) -> None:
    people = spec.get("people", []) or []
    systems = spec.get("systems", []) or []
    external_systems = spec.get("external_systems", []) or []
    relations = spec.get("relations", []) or []

    nodes = [*people, *systems, *external_systems]
    if not systems:
        raise ValueError("C4 spec must include at least one system in `systems`.")

    valid_ids: set[str] = set()
    for item in nodes:
        node_id = _node_id(item)
        if node_id == "node":
            raise ValueError("Every C4 node must include a non-empty `name` or `id`.")
        valid_ids.add(node_id)

    for idx, rel in enumerate(relations, start=1):
        src = _slug_id(str(rel.get("from") or ""))
        dst = _slug_id(str(rel.get("to") or ""))
        if src == "node" or dst == "node":
            raise ValueError(f"Relation {idx} must include non-empty `from` and `to`.")
        if src not in valid_ids:
            raise ValueError(f"Relation {idx} references unknown source `{rel.get('from')}`.")
        if dst not in valid_ids:
            raise ValueError(f"Relation {idx} references unknown target `{rel.get('to')}`.")


def render_c4_context_plantuml(title: str, spec: dict[str, Any]) -> str:
    people = spec.get("people", []) or []
    systems = spec.get("systems", []) or []
    external_systems = spec.get("external_systems", []) or []
    relations = spec.get("relations", []) or []

    lines = ["@startuml", "!include <C4/C4_Context>", "", f"title {title}", ""]

    for item in people:
        lines.append(
            f'Person({_node_id(item)}, "{item.get("name", "Unknown")}", "{item.get("description", "")}")'
        )

    for item in systems:
        lines.append(
            f'System({_node_id(item)}, "{item.get("name", "System")}", "{item.get("description", "")}")'
        )

    for item in external_systems:
        node_id = _node_id(item)
        name = item.get("name", "External System")
        desc = item.get("description", "")
        kind = str(item.get("kind", "system")).lower()
        if kind == "database":
            lines.append(f'SystemDb_Ext({node_id}, "{name}", "{desc}")')
        elif kind == "queue":
            lines.append(f'SystemQueue_Ext({node_id}, "{name}", "{desc}")')
        else:
            lines.append(f'System_Ext({node_id}, "{name}", "{desc}")')

    if relations:
        lines.append("")
    for item in relations:
        src = _slug_id(str(item.get("from", "")))
        dst = _slug_id(str(item.get("to", "")))
        label = item.get("label", "Uses")
        technology = item.get("technology", "")
        bidirectional = bool(item.get("bidirectional", False))
        rel_name = "BiRel" if bidirectional else "Rel"
        if technology:
            lines.append(f'{rel_name}({src}, {dst}, "{label}", "{technology}")')
        else:
            lines.append(f'{rel_name}({src}, {dst}, "{label}")')

    lines.append("@enduml")
    return "\n".join(lines)


def write_c4_artifact(
    output_root: str,
    filename: str,
    title: str,
    summary: str,
    spec_json: str,
) -> str:
    spec = json.loads(spec_json)
    _validate_c4_spec(spec)
    diagram = render_c4_context_plantuml(title, spec)
    heading = "# C4 Context Diagram"
    subheading = "## System Context Diagram"
    if "target-state/" in filename:
        heading = "# C4 Target Diagram"
        subheading = "## Target System Context Diagram"
    content = (
        f"{heading}\n\n"
        f"{summary.strip()}\n\n"
        f"{subheading}\n\n"
        "```plantuml\n"
        f"{diagram}\n"
        "```\n"
    )
    return _write_artifact(output_root, filename, content)


WRITE_C4_ARTIFACT_DEF = ToolDefinition(
    name="write_c4_artifact",
    description=(
        "Persist a C4 C1 system-context artifact from structured data."
    ),
    params=[
        ToolParam(
            name="filename",
            type="string",
            description="Relative output path for the C4 context artifact.",
        ),
        ToolParam(
            name="title",
            type="string",
            description="Diagram title shown in the PlantUML C4 context diagram.",
        ),
        ToolParam(
            name="summary",
            type="string",
            description="Short paragraph introducing the system context.",
        ),
        ToolParam(
            name="spec_json",
            type="string",
            description=(
                "JSON object with people, systems, external_systems, and relations arrays. "
                "external_systems items may use kind=system|database|queue."
            ),
        ),
    ],
)
WRITE_C4_ARTIFACT_OPENAI = WRITE_C4_ARTIFACT_DEF.to_openai_dict()
WRITE_C4_ARTIFACT_ANTHROPIC = WRITE_C4_ARTIFACT_DEF.to_anthropic_dict()
