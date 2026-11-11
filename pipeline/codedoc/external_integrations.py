"""Deterministic `current-state/external-integrations.md` rendering from outbound-call graph data."""

from __future__ import annotations

import re

from codedoc.kg_tools.toolkit import ReverseEngineerToolkit
from codedoc.kg_tools.utils import format_chain, short_node_name

ARTIFACT_PATH = "current-state/external-integrations.md"
_MAX_HOSTS_IN_DIAGRAM = 20
_MAX_ENDPOINTS_PER_HOST = 25


def _mermaid_label(text: str) -> str:
    return re.sub(r'["\n\r]', "'", text)[:60]


def _cell(text: object) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


_MAX_MAP_APIS = 30
_MAX_MAP_FLOWS_PER_API = 2


def _node_label(text: str) -> str:
    return re.sub(r'["\n\r<>]', "'", text)[:70]


def _api_path(api: dict) -> str:
    url, host = api["url"], api["host"]
    idx = url.find(host)
    rest = url[idx + len(host):] if idx >= 0 else url
    return rest or "/"


def _host_label(host: str) -> str:
    return f"Runtime-resolved: {host.strip('{}')}" if _is_unresolved_host(host) else host


def render_integration_flow_map(repo_name: str, apis: list[dict]) -> list[str]:
    """Mermaid flowchart: inbound endpoint -> call chain -> outbound API, grouped by remote host."""
    ids: dict[str, str] = {}
    declared: set[str] = set()
    edges: set[tuple[str, str, str]] = set()
    node_lines: list[str] = []
    edge_lines: list[str] = []

    def node(key: str, label: str, shape: str = "[{}]", css: str = "") -> str:
        if key not in ids:
            ids[key] = f"n{len(ids)}"
        nid = ids[key]
        if nid not in declared:
            declared.add(nid)
            node_lines.append(f'    {nid}{shape.format(chr(34) + _node_label(label) + chr(34))}{css}')
        return nid

    def edge(a: str, b: str, label: str = "") -> None:
        if (a, b, label) not in edges:
            edges.add((a, b, label))
            edge_lines.append(f'    {a} -->|"{_node_label(label)}"| {b}' if label else f"    {a} --> {b}")

    by_host: dict[str, list[dict]] = {}
    for api in apis[:_MAX_MAP_APIS]:
        by_host.setdefault(api["host"], []).append(api)

    group_lines: list[str] = []
    for hindex, (host, items) in enumerate(sorted(by_host.items(), key=lambda kv: (-len(kv[1]), kv[0]))):
        group_lines.append(f'    subgraph g{hindex}["{_node_label(_host_label(host))}"]')
        for api in items:
            aid = node(f"api:{api['method']} {api['url']}", f"{api['method']} {_api_path(api)}", "([{}])", ":::ext")
            ids_in_group = ids[f"api:{api['method']} {api['url']}"]
            group_lines.append(f"        {ids_in_group}")
            client = api.get("client") or ""
            flows = api.get("flows", [])[:_MAX_MAP_FLOWS_PER_API]
            if flows:
                for flow in flows:
                    entry = node(f"entry:{flow['entry']}", flow["entry"], "[/{}/]", ":::entry")
                    prev = entry
                    for node_id in flow["chain"]:
                        cur = node(f"fn:{node_id}", short_node_name(node_id))
                        edge(prev, cur)
                        prev = cur
                    edge(prev, aid, client)
            else:
                for caller in api["callers"][:2]:
                    name = short_node_name(re.sub(r"\(.*\)$", "", caller["caller"]))
                    cur = node(f"fn:{caller['caller']}", name, "[{}]", ":::bg")
                    edge(cur, aid, client)
        group_lines.append("    end")

    return [
        "```mermaid",
        "flowchart LR",
        *node_lines,
        *group_lines,
        *edge_lines,
        "    classDef entry fill:#e8f1ff,stroke:#3b6fd4",
        "    classDef ext fill:#fff4e0,stroke:#d98a00",
        "    classDef bg stroke-dasharray: 4 2",
        "```",
        "",
        "_Blue = inbound HTTP endpoint, boxes = the code path that reaches the call, orange = outbound API. "
        "Dashed boxes have no known inbound endpoint (listeners, startup hooks, scheduled jobs or unresolved dispatch)._\n",
    ]


_MAX_CONTRACTS = 40
_PARAM_LABELS = {"path": "Path param", "query": "Query param", "form": "Form field"}


_JSON_SCALARS: dict[str, tuple[str, str | None]] = {
    **dict.fromkeys(("string", "str", "char", "character"), ("string", None)),
    "uuid": ("string", "uuid"),
    **dict.fromkeys(("date", "localdatetime", "offsetdatetime", "zoneddatetime", "instant"), ("string", "date-time")),
    "localdate": ("string", "date"),
    "localtime": ("string", "time"),
    "duration": ("string", "duration"),
    **dict.fromkeys(("url", "uri"), ("string", "uri")),
    **dict.fromkeys(("bytes", "byte[]"), ("string", "byte")),
    **dict.fromkeys(("int", "integer", "long", "short", "byte", "biginteger"), ("integer", None)),
    **dict.fromkeys(("double", "float", "number", "bigdecimal"), ("number", None)),
    **dict.fromkeys(("boolean", "bool"), ("boolean", None)),
    **dict.fromkeys(("map", "hashmap", "object", "jsonnode"), ("object", None)),
}


def _type_schema(type_text: str) -> dict:
    """Schema for a declared type with no resolved fields (scalar, collection of scalars, or unresolved class)."""
    base, is_list = ReverseEngineerToolkit._peel_type(type_text)
    if not base or base.lower() in {"any", "unknown", "void"}:
        item: dict = {}
    elif base.lower() in _JSON_SCALARS:
        kind, fmt = _JSON_SCALARS[base.lower()]
        item = {"type": kind, **({"format": fmt} if fmt else {})}
    else:
        item = {"type": "object", "description": f"{base} (fields not resolved)"}
    return {"type": "array", "items": item} if is_list else item


def _inline_schema(names: list[str]) -> dict:
    """Object schema from usage-inferred dotted field names (`items.id` -> items: {id})."""
    tree: dict = {}
    for name in names:
        node = tree
        for part in name.split("."):
            node = node.setdefault(part.removesuffix("[]"), {})

    def build(node: dict) -> dict:
        if not node:
            return {}
        return {"type": "object", "properties": {k: build(v) for k, v in node.items()}}

    return build(tree) or {"type": "object"}


class _SchemaSet:
    """Named object schemas (OpenAPI `components.schemas`) collected for one call; nested types become `$ref`s."""

    def __init__(self) -> None:
        self.defs: dict[str, dict] = {}

    @staticmethod
    def _ref(name: str) -> dict:
        return {"$ref": f"#/components/schemas/{name}"}

    def _define(self, name: str, fields: list[dict]) -> dict:
        if name not in self.defs:
            self.defs[name] = {}  # reserved first so a self-referencing type resolves to its own $ref
            self.defs[name] = {"type": "object", "properties": {f["name"]: self.field(f) for f in fields}}
        return self._ref(name)

    def field(self, field: dict) -> dict:
        text = field.get("type") or ""
        base, is_list = ReverseEngineerToolkit._peel_type(text)
        if field.get("fields"):
            node = self._define(base or "Object", field["fields"])
            is_list = bool(field.get("list"))
        elif base in self.defs and base.lower() not in _JSON_SCALARS:
            node = self._ref(base)
        else:
            return _type_schema(text)
        return {"type": "array", "items": node} if is_list else node

    def body(self, shape: dict) -> dict:
        if shape.get("inline"):
            node: dict = _inline_schema([f["name"] for f in shape["fields"]])
        elif shape.get("fields"):
            node = self._define(shape["type"], shape["fields"])
        else:
            return _type_schema(f"List<{shape['type']}>" if shape.get("list") else shape["type"])
        return {"type": "array", "items": node} if shape.get("list") else node


_PLAIN_YAML = re.compile(r"^[\w./-]+$")


def _yaml_scalar(value: object) -> str:
    text = str(value)
    return text if _PLAIN_YAML.match(text) else "'" + text.replace("'", "''") + "'"


def _yaml_flow(node: dict) -> str:
    return "{ " + ", ".join(f"{k}: {_yaml_scalar(v)}" for k, v in node.items()) + " }" if node else "{}"


def _yaml_lines(node: dict, indent: int = 0) -> list[str]:
    """OpenAPI-style YAML: scalar-only schemas collapse to `{ type: string }`, nested ones stay in block form."""
    pad = " " * indent
    out: list[str] = []
    for key, value in node.items():
        name = _yaml_scalar(key)
        if not isinstance(value, dict):
            out.append(f"{pad}{name}: {_yaml_scalar(value)}")
        elif not value or all(not isinstance(v, dict) for v in value.values()):
            out.append(f"{pad}{name}: {_yaml_flow(value)}")
        else:
            out.append(f"{pad}{name}:")
            out.extend(_yaml_lines(value, indent + 2))
    return out


def _body_yaml(contract: dict) -> list[str]:
    """YAML block with the request/response bodies and every named schema they use."""
    schemas = _SchemaSet()
    doc: dict = {}
    if contract.get("request"):
        doc["request"] = schemas.body(contract["request"])
    if contract.get("response"):
        doc["response"] = schemas.body(contract["response"])
    if schemas.defs:
        doc["components"] = {"schemas": schemas.defs}
    lines = _yaml_lines(doc)
    if not contract.get("response"):
        lines.append("# response type not detected")
    return ["```yaml", *lines, "```", ""]


def _has_contract(api: dict) -> bool:
    c = api.get("contract") or {}
    return bool(c.get("request") or c.get("response") or c.get("params") or c.get("headers"))


def render_api_contracts(apis: list[dict]) -> list[str]:
    """`## API Contracts` section: request params/headers/body and response shape per outbound call."""
    documented = [a for a in apis if _has_contract(a)]
    if not documented:
        return []
    lines = [
        "## API Contracts [Observed]\n",
        "Request and response details for each outbound call, read statically from the code: types come from "
        "declared Java/TypeScript types, annotations and call arguments. Bodies are OpenAPI-style schemas "
        "(`components.schemas` with `$ref`) resolved from the indexed classes at every nesting level. Plain-JavaScript "
        "bodies list only the fields the calling code actually reads, and a type marked \"fields not resolved\" is "
        "external or could not be found.\n",
    ]
    for api in documented[:_MAX_CONTRACTS]:
        contract = api["contract"]
        lines.append(f"### `{_cell(api['method'])} {_cell(api['url'])}`\n")
        if api.get("callers"):
            lines.append(f"Called from `{_cell(api['callers'][0]['caller'])}`"
                         + (f" (+{len(api['callers']) - 1} more)" if len(api["callers"]) > 1 else "") + "\n")

        lines.append("**Request**\n")
        rows: list[str] = []
        for item in contract.get("params") or []:
            kind, _, name = item.partition(":")
            rows.append(f"| {_PARAM_LABELS.get(kind, kind)} | `{_cell(name)}` |")
        for header in contract.get("headers") or []:
            rows.append(f"| Header | `{_cell(header)}` |")
        if rows:
            lines.extend(["| Part | Name |", "|---|---|", *rows, ""])
        else:
            lines.append("_No parameters or headers detected._\n")

        lines.append("**Request / response bodies**\n")
        if contract.get("request") or contract.get("response"):
            lines.extend(_body_yaml(contract))
        else:
            lines.append("_No request or response body type detected._\n")
    if len(documented) > _MAX_CONTRACTS:
        lines.append(f"_{len(documented) - _MAX_CONTRACTS} more call(s) not shown._\n")
    return lines


def render_external_integrations(repo_name: str, apis: list[dict]) -> str | None:
    """Render the artifact markdown, or None when no outbound calls were detected."""
    if not apis:
        return None

    by_host: dict[str, list[dict]] = {}
    for api in apis:
        by_host.setdefault(api["host"], []).append(api)
    hosts = sorted(by_host.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    lines = [
        "# External Integrations\n",
        "## Overview [Observed]\n",
        f"`{repo_name}` makes **{len(apis)}** distinct outbound HTTP call target(s) to "
        f"**{len(hosts)}** remote host(s). The list below is generated directly from the knowledge graph "
        "(`ExternalApi` nodes and `CALLS_EXTERNAL` edges), not written by a model.\n",
        "Placeholders such as `{id}` are values computed at runtime; `${NAME}` is an environment or "
        "configuration value the indexer could not resolve to a literal.\n",
        "## Integration Map [Observed]\n",
    ]
    lines.extend(render_integration_flow_map(repo_name, apis))
    if len(apis) > _MAX_MAP_APIS:
        lines.append(f"_Diagram shows {_MAX_MAP_APIS} of {len(apis)} endpoints; see the tables below for the full list._\n")

    lines.append("## Remote Hosts [Observed]\n")
    for host, items in hosts:
        clients = sorted({api["client"] for api in items if api.get("client")})
        languages = sorted({api["language"] for api in items if api.get("language")})
        lines.append(f"### `{host}`\n")
        lines.append(f"Clients: {', '.join(clients) or 'n/a'} · Languages: {', '.join(languages) or 'n/a'}\n")
        lines.append("| Method | URL | Called from | Inbound flow |")
        lines.append("|---|---|---|---|")
        for api in items[:_MAX_ENDPOINTS_PER_HOST]:
            callers = []
            for caller in api["callers"][:3]:
                where = f" ({caller['path']}:{caller['line']})" if caller.get("path") and caller.get("line") else ""
                callers.append(f"`{caller['caller']}`{where}")
            if len(api["callers"]) > 3:
                callers.append(f"+{len(api['callers']) - 3} more")
            flows = [f"`{f['entry']}`: {format_chain(f['chain'])}" for f in api.get("flows", [])[:3]]
            entry_points = "<br>".join(flows) or "—"
            lines.append(
                f"| {_cell(api['method'])} | `{_cell(api['url'])}` | {_cell('<br>'.join(callers))} | {_cell(entry_points)} |"
            )
        if len(items) > _MAX_ENDPOINTS_PER_HOST:
            lines.append(f"\n_{len(items) - _MAX_ENDPOINTS_PER_HOST} more endpoint(s) not shown._")
        lines.append("")

    lines.extend(render_api_contracts(apis))

    lines.extend(
        [
            "## Interpretation [Inferred]\n",
            "- Each host above is a runtime dependency: an outage or contract change there affects the calling code listed.",
            "- The 'Inbound flow' column is best-effort: it follows resolved call edges back to HTTP entry points "
            "and may be empty when the call is made from a scheduler, listener, or through dynamic dispatch.",
            "- Calls whose URL is built entirely at runtime or read from an unresolved config key are shown with placeholders "
            "and carry lower confidence.",
            "",
        ]
    )
    return "\n".join(lines)


# ── C4 context: guarantee every outbound host is a node ──────────────────────

_MAX_C4_HOSTS = 15
_PLACEHOLDER_HOST = re.compile(r"^\$\{(.+)\}$")


def _host_display_name(host: str) -> str:
    match = _PLACEHOLDER_HOST.match(host)
    return f"Configured service ({match.group(1)})" if match else host


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is_unresolved_host(host: str) -> bool:
    """Hosts computed at runtime (e.g. `{getUri()}`) are not meaningful C4 nodes."""
    return "(" in host or host.startswith("{")


def _covered(host: str, names: str, described: str) -> bool:
    """True if the model already drew this host (by name/id, or by mentioning it)."""
    if _squash(host) in described:
        return True
    if "." in host or _PLACEHOLDER_HOST.match(host):
        return False
    # Internal service names like `customers-service` are usually drawn as "Customers Service".
    base = _squash(re.sub(r"[-_]?(service|svc|server|api)$", "", host, flags=re.I))
    return len(base) >= 3 and base in names


def merge_outbound_into_c4_spec(spec: dict, apis: list[dict]) -> dict:
    """Add a System_Ext + HTTP/REST relation for every outbound host the model did not already cover."""
    systems = spec.get("systems") or []
    if not apis or not systems:
        return spec
    primary = systems[0].get("id") or systems[0].get("name")
    externals = list(spec.get("external_systems") or [])
    relations = list(spec.get("relations") or [])
    nodes = [*(spec.get("people") or []), *systems, *externals]
    names = _squash(" ".join(f"{n.get('id', '')} {n.get('name', '')}" for n in nodes))
    described = _squash(" ".join(f"{n.get('description', '')}" for n in nodes)) + names
    used_ids = {re.sub(r"[^A-Za-z0-9_]+", "_", str(e.get("id") or e.get("name") or "")) for e in externals}

    by_host: dict[str, list[dict]] = {}
    for api in apis:
        by_host.setdefault(api["host"], []).append(api)
    for host, items in sorted(by_host.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:_MAX_C4_HOSTS]:
        if _is_unresolved_host(host) or _covered(host, names, described):
            continue
        node_id = "out_" + re.sub(r"[^A-Za-z0-9]+", "_", host).strip("_")
        if node_id in used_ids:
            continue
        used_ids.add(node_id)
        count = len(items)
        externals.append(
            {
                "id": node_id,
                "name": _host_display_name(host).replace('"', "'"),
                "description": f"Outbound HTTP, {count} endpoint{'s' if count != 1 else ''}",
                "kind": "system",
            }
        )
        relations.append({"from": primary, "to": node_id, "label": "Calls", "technology": "HTTP/REST"})
    return {**spec, "external_systems": externals, "relations": relations}


# ── Business journeys: deterministic per-endpoint outbound sequence diagrams ──

JOURNEYS_MARKER = "## External Calls By Endpoint"
_MAX_JOURNEY_DIAGRAMS = 8


def _mm(text: str) -> str:
    return re.sub(r"[;#\n\r]", " ", str(text))


def _path_of(url: str, host: str) -> str:
    idx = url.find(host)
    rest = url[idx + len(host):] if idx >= 0 else url
    return rest or "/"


def render_outbound_journeys(apis: list[dict]) -> str | None:
    """Mermaid sequence diagrams: inbound endpoint -> call chain -> remote host. None if no flow is known."""
    flows: dict[str, list[tuple[list[str], dict]]] = {}
    for api in apis:
        for flow in api.get("flows", []):
            flows.setdefault(flow["entry"], []).append((flow["chain"], api))
    if not flows:
        return None

    lines = [
        JOURNEYS_MARKER + " [Observed]\n",
        "Generated from the knowledge graph: each diagram follows resolved call edges from an HTTP endpoint "
        "to the outbound HTTP call it can trigger.\n",
    ]
    for entry, items in list(flows.items())[:_MAX_JOURNEY_DIAGRAMS]:
        participants: dict[str, str] = {}

        def pid(label: str) -> str:
            if label not in participants:
                participants[label] = f"p{len(participants)}"
            return participants[label]

        body: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        pid("Client")
        for chain, api in items:
            names = [short_node_name(n) for n in chain]
            classes = [n.rsplit(".", 1)[0] if "." in n else n for n in names]
            methods = [n.rsplit(".", 1)[-1] for n in names]
            prev = "Client"
            for index, (cls, method) in enumerate(zip(classes, methods)):
                message = f"{entry} ({method})" if index == 0 else method
                edge = (prev, cls, message)
                if edge not in seen:
                    seen.add(edge)
                    body.append(f"    {pid(prev)}->>{pid(cls)}: {_mm(message)}")
                prev = cls
            edge = (prev, api["host"], f"{api['method']} {api['url']}")
            if edge not in seen:
                seen.add(edge)
                body.append(
                    f"    {pid(prev)}->>{pid(api['host'])}: {_mm(api['method'] + ' ' + _path_of(api['url'], api['host']))}"
                )
        lines.append(f"### `{entry}`\n")
        lines.append("```mermaid\nsequenceDiagram")
        lines.extend(f'    participant {p} as {_mm(label).replace(chr(34), chr(39))}' for label, p in participants.items())
        lines.extend(body)
        lines.append("```\n")
    return "\n".join(lines)
