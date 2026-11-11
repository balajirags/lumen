from codedoc.artifact_planner import build_artifact_plan
from codedoc.diagrams.c4 import write_c4_artifact
from codedoc.external_integrations import (
    merge_outbound_into_c4_spec,
    render_external_integrations,
    render_outbound_journeys,
)
from codedoc.kg_tools import ReverseEngineerToolkit
from codedoc.stages.agent import _append_outbound_journeys, _backfill_required_artifacts


class FakeBackend:
    """Answers the handful of Cypher shapes the outbound-call helpers issue."""

    def __init__(self, outbound_rows, workflows, calls):
        self.outbound_rows = outbound_rows
        self.workflows = workflows
        self.calls = calls  # callee id -> [caller ids]

    def execute(self, cypher, *args, **kwargs):
        if "CALLS_EXTERNAL" in cypher:
            return self.outbound_rows
        if "MATCH (w:Workflow)" in cypher and "entryPointId" in cypher:
            return self.workflows
        if "[c:CALLS]" in cypher:
            rows = []
            for callee, callers in self.calls.items():
                if f"'{callee}'" in cypher:
                    rows.extend({"src": c, "dst": callee} for c in callers)
            return rows
        return []


def _toolkit(outbound_rows=(), workflows=(), calls=None):
    return ReverseEngineerToolkit(FakeBackend(list(outbound_rows), list(workflows), calls or {}))


ROW = {
    "api_id": "extapi:GET https://pay.example.com/v1/charges/{id}",
    "method": "GET",
    "url": "https://pay.example.com/v1/charges/{id}",
    "host": "pay.example.com",
    "client": "RestTemplate",
    "language": "java",
    "caller_id": "m:PaymentClient.charge",
    "caller": "com.acme.PaymentClient.charge",
    "caller_type": "Method",
    "path": "src/PaymentClient.java",
    "line": 42,
    "confidence": 0.9,
}


def test_outbound_api_calls_resolves_inbound_entry_points():
    toolkit = _toolkit(
        [ROW],
        workflows=[{"id": "m:OrderController.create", "method": "POST", "path": "/orders"}],
        calls={"m:PaymentClient.charge": ["m:OrderService.place"], "m:OrderService.place": ["m:OrderController.create"]},
    )

    apis = toolkit.outbound_api_calls()

    assert len(apis) == 1
    assert apis[0]["host"] == "pay.example.com"
    assert apis[0]["entry_points"] == ["POST /orders"]
    text = toolkit.call("get_outbound_api_calls")
    assert "pay.example.com" in text and "PaymentClient.charge" in text and "POST /orders" in text


def test_outbound_api_calls_empty_graph_reports_none():
    toolkit = _toolkit()
    assert toolkit.outbound_api_calls() == []
    assert toolkit.call("get_outbound_api_calls") == "No outbound HTTP calls detected."


def test_render_external_integrations_none_when_empty():
    assert render_external_integrations("svc", []) is None


def test_render_external_integrations_contains_host_table_and_diagram():
    apis = _toolkit([ROW]).outbound_api_calls()
    content = render_external_integrations("orders-svc", apis)
    assert "```mermaid" in content and "pay.example.com" in content
    assert "| GET | `https://pay.example.com/v1/charges/{id}` |" in content


def test_plan_includes_external_integrations_as_conditional():
    for repo_type in ("backend-service", "frontend-app", "fullstack-app", "library"):
        item = next(i for i in build_artifact_plan(repo_type)["artifacts"] if i["path"] == "current-state/external-integrations.md")
        assert item["class"] == "conditional" and not item["required"]


def test_backfill_writes_external_integrations_only_when_calls_exist(tmp_path, monkeypatch):
    monkeypatch.setattr("codedoc.stages.agent.KuzuBackend", lambda path: object())
    with_calls = _toolkit([ROW])
    monkeypatch.setattr("codedoc.stages.agent.ReverseEngineerToolkit", lambda backend, repo_path="": with_calls)
    generated = _backfill_required_artifacts("db", "repo", str(tmp_path / "a"), "backend-service", repo_name="orders")
    assert any(g.endswith("external-integrations.md") for g in generated)
    assert "orders" in (tmp_path / "a" / "current-state" / "external-integrations.md").read_text()

    without = _toolkit()
    monkeypatch.setattr("codedoc.stages.agent.ReverseEngineerToolkit", lambda backend, repo_path="": without)
    generated = _backfill_required_artifacts("db", "repo", str(tmp_path / "b"), "backend-service")
    assert not (tmp_path / "b" / "current-state" / "external-integrations.md").exists()


CHAIN_CALLS = {
    "m:com.acme.PaymentClient.charge(String)": ["m:com.acme.OrderService.place(String)"],
    "m:com.acme.OrderService.place(String)": ["m:com.acme.OrderController.create(String)"],
}
CHAIN_ROW = {**ROW, "caller_id": "m:com.acme.PaymentClient.charge(String)", "caller": "com.acme.PaymentClient.charge(String)"}
CHAIN_WORKFLOWS = [{"id": "m:com.acme.OrderController.create(String)", "method": "POST", "path": "/orders"}]


def _chain_apis():
    return _toolkit([CHAIN_ROW], CHAIN_WORKFLOWS, CHAIN_CALLS).outbound_api_calls()


def test_flows_carry_full_call_chain():
    flow = _chain_apis()[0]["flows"][0]
    assert flow["entry"] == "POST /orders"
    assert flow["chain"] == [
        "m:com.acme.OrderController.create(String)",
        "m:com.acme.OrderService.place(String)",
        "m:com.acme.PaymentClient.charge(String)",
    ]


def test_journeys_render_sequence_diagram_through_to_host():
    section = render_outbound_journeys(_chain_apis())
    assert "sequenceDiagram" in section
    for name in ("OrderController", "OrderService", "PaymentClient", "pay.example.com"):
        assert f" as {name}" in section
    assert "GET /v1/charges/{id}" in section
    assert render_outbound_journeys([dict(a, flows=[]) for a in _chain_apis()]) is None


def test_c4_merge_adds_missing_hosts_only():
    spec = {
        "people": [{"id": "user", "name": "User"}],
        "systems": [{"id": "sys", "name": "Orders"}],
        "external_systems": [{"id": "pay", "name": "Payment Gateway", "description": "pay.example.com"}],
        "relations": [],
    }
    apis = _chain_apis() + [dict(_chain_apis()[0], host="ship.example.com")]
    merged = merge_outbound_into_c4_spec(spec, apis)
    names = [e["name"] for e in merged["external_systems"]]
    assert names == ["Payment Gateway", "ship.example.com"]
    assert merged["relations"] == [
        {"from": "sys", "to": "out_ship_example_com", "label": "Calls", "technology": "HTTP/REST"}
    ]


def test_write_c4_artifact_merges_outbound_but_not_for_target_state(tmp_path):
    spec = '{"systems": [{"id": "sys", "name": "Orders"}], "external_systems": [], "relations": []}'
    write_c4_artifact(str(tmp_path), "architecture/c4-context.md", "C4", "s", spec, outbound_apis=_chain_apis())
    assert "pay.example.com" in (tmp_path / "architecture/c4-context.md").read_text()
    write_c4_artifact(str(tmp_path), "target-state/c4-target.md", "C4", "s", spec, outbound_apis=_chain_apis())
    assert "pay.example.com" not in (tmp_path / "target-state/c4-target.md").read_text()


def test_workflow_steps_append_outbound_hops(tmp_path):
    class Backend(FakeBackend):
        def execute(self, cypher, *a, **k):
            if "WORKFLOW_STEP" in cypher:
                return [{"step": 1, "node": "create", "qualifiedName": "x"}]
            if "RETURN w.entryPointId AS id LIMIT 1" in cypher:
                return [{"id": "m:com.acme.OrderController.create(String)"}]
            return super().execute(cypher, *a, **k)

    toolkit = ReverseEngineerToolkit(Backend([CHAIN_ROW], CHAIN_WORKFLOWS, CHAIN_CALLS))
    out = toolkit.call("get_workflow_steps", workflow_name="W")
    assert "OrderController.create → OrderService.place → PaymentClient.charge → GET" in out


def test_backfill_appends_journeys_once(tmp_path, monkeypatch):
    art = tmp_path / "a"
    (art / "architecture").mkdir(parents=True)
    (art / "architecture" / "business-journeys.md").write_text("# Journeys\n")
    toolkit = _toolkit([CHAIN_ROW], CHAIN_WORKFLOWS, CHAIN_CALLS)
    monkeypatch.setattr("codedoc.stages.agent.KuzuBackend", lambda path: object())
    monkeypatch.setattr("codedoc.stages.agent.ReverseEngineerToolkit", lambda backend, repo_path="": toolkit)
    _backfill_required_artifacts("db", "repo", str(art), "backend-service")
    _backfill_required_artifacts("db", "repo", str(art), "backend-service")
    text = (art / "architecture" / "business-journeys.md").read_text()
    assert text.count("## External Calls By Endpoint") == 1 and "sequenceDiagram" in text


def test_flows_follow_edges_to_unresolved_signature_callees():
    calls = {
        "m:com.acme.PaymentClient.charge(?)": ["m:com.acme.OrderController.create(String)"],
    }
    flow = _toolkit([CHAIN_ROW], CHAIN_WORKFLOWS, calls).outbound_api_calls()[0]["flows"][0]
    assert flow["chain"] == [
        "m:com.acme.OrderController.create(String)",
        "m:com.acme.PaymentClient.charge(String)",
    ]


def test_c4_merge_skips_hosts_the_model_already_drew_and_runtime_hosts():
    spec = {
        "systems": [{"id": "gw", "name": "Gateway"}],
        "external_systems": [{"id": "cust", "name": "Customers Service"}],
        "relations": [],
    }
    base = _chain_apis()[0]
    apis = [
        dict(base, host="customers-service"),
        dict(base, host="{getUri()}"),
        dict(base, host="pay.example.com"),
    ]
    merged = merge_outbound_into_c4_spec(spec, apis)
    assert [e["name"] for e in merged["external_systems"]] == ["Customers Service", "pay.example.com"]


def test_journeys_are_appended_to_user_journeys_for_fullstack_plans(tmp_path):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "architecture" / "user-journeys.md").write_text("# User Journeys\n")
    toolkit = _toolkit([CHAIN_ROW], CHAIN_WORKFLOWS, CHAIN_CALLS)
    assert _append_outbound_journeys(toolkit, str(tmp_path))
    assert _append_outbound_journeys(toolkit, str(tmp_path)) is None
    assert "sequenceDiagram" in (tmp_path / "architecture" / "user-journeys.md").read_text()


def test_integration_map_shows_full_flow_from_endpoint_to_outbound_api():
    apis = _chain_apis()
    content = render_external_integrations("orders", apis)
    for label in ("POST /orders", "OrderController.create", "OrderService.place", "PaymentClient.charge", "GET /v1/charges/{id}"):
        assert label in content
    assert 'subgraph g0["pay.example.com"]' in content
    assert "RestTemplate" in content
    assert "OrderController.create → OrderService.place → PaymentClient.charge" in content


def test_integration_map_marks_calls_without_inbound_endpoint_and_runtime_hosts():
    row = dict(ROW, host="{getUri()}", url="{getUri()}/owners")
    content = render_external_integrations("orders", _toolkit([row]).outbound_api_calls())
    assert 'subgraph g0["Runtime-resolved: getUri()"]' in content
    assert ":::bg" in content and "PaymentClient.charge" in content
