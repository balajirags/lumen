from __future__ import annotations

from codedoc.kg_tools.toolkit import ReverseEngineerToolkit


class FakeBackend:
    def execute(self, query: str):
        if "MATCH (entry)" in query and "ROUTE COMPONENT MAP" not in query and "entry.qualifiedName AS entry" in query:
            return [
                {
                    "entry": "app.routes.InventoryPage",
                    "entry_type": "Component",
                    "path": "src/pages/InventoryPage.tsx",
                    "owner": "app.routes",
                }
            ]
        if "MATCH (entry)-[r:RENDERS]->(child:Component)" in query:
            return [
                {
                    "entry": "app.routes.InventoryPage",
                    "child_component": "app.components.ReserveModal",
                    "line": 42,
                }
            ]
        if "MATCH (c:Component) " in query and "NOT EXISTS { MATCH ()-[:RENDERS]->(c) }" in query:
            return [{"root": "app.AppShell", "path": "src/App.tsx"}]
        if "MATCH (a)-[r:RENDERS]->(b:Component)" in query:
            return [{"parent": "app.AppShell", "child": "app.routes.InventoryPage", "line": 12}]
        if "MATCH (consumer)-[r:USES_HOOK]->(h:Hook)" in query:
            return [{"consumer": "app.routes.InventoryPage", "consumer_type": "Component", "hook": "app.hooks.useInventoryData", "line": 16}]
        if "MATCH (h:Hook)-[r:CALLS]->(target)" in query:
            return [{"hook": "app.hooks.useInventoryData", "target": "api.inventoryClient.getItems", "line": 27}]
        if "MATCH (state) " in query and "state_owner" in query:
            return [{"state_owner": "app.hooks.useInventoryData", "state_type": "Hook", "path": "src/hooks/useInventoryData.ts"}]
        if "MATCH (consumer)-[:USES_HOOK|CALLS|IMPORTS]->(state)" in query and "state_owner" in query:
            return [{"consumer": "app.routes.InventoryPage", "consumer_type": "Component", "state_owner": "app.hooks.useInventoryData", "state_type": "Hook"}]
        if "MATCH (ui)-[r:CALLS]->(target)" in query:
            return [
                {
                    "ui": "app.routes.InventoryPage",
                    "ui_type": "Component",
                    "ui_path": "src/pages/InventoryPage.tsx",
                    "client": "api.inventoryClient.getItems",
                    "client_path": "src/api/inventoryClient.ts",
                    "line": 27,
                }
            ]
        if "MATCH (n) WHERE n.normKind = 'Entrypoint'" in query:
            return [{"endpoint": "InventoryController.getInventory", "endpoint_name": "getInventory", "endpoint_type": "Method"}]
        if "MATCH (owner)-[:CONTAINS]->(c:Component)" in query:
            return [{"owner_name": "app.routes", "component_name": "app.routes.InventoryPage", "path": "src/pages/InventoryPage.tsx"}]
        if "MATCH (a)-[:PROP_DEPENDENCY]->(b)" in query:
            return [{"owner": "app.routes.InventoryPage", "dependency": "app.components.ReserveModal"}]
        if "MATCH (h:Hook) " in query:
            return [{"hook": "app.hooks.useInventoryData", "path": "src/hooks/useInventoryData.ts"}]
        if "MATCH (n) " in query and "store|context|provider|state|query|cache|reducer" in query:
            return [{"type": "Component", "name": "app.providers.InventoryQueryProvider", "path": "src/providers/InventoryQueryProvider.tsx"}]
        if "MATCH (caller)-[r:CALLS]->(target)" in query and "caller_type" in query:
            return [{"caller": "app.hooks.useInventoryData", "caller_type": "Hook", "target": "api.inventoryClient.getItems", "line": 27}]
        return []


def test_get_ui_to_api_call_map_links_ui_clients_and_probable_endpoints():
    toolkit = ReverseEngineerToolkit(FakeBackend(), repo_path=".")

    result = toolkit.call("get_ui_to_api_call_map")

    assert "UI TO API CALL MAP" in result
    assert "InventoryPage" in result
    assert "inventoryClient.getItems" in result
    assert "InventoryController.getInventory" in result


def test_get_frontend_architecture_summary_uses_frontend_specific_tools():
    toolkit = ReverseEngineerToolkit(FakeBackend(), repo_path=".")

    result = toolkit.call("get_frontend_architecture_summary")

    assert "ROUTE COMPONENT MAP" in result
    assert "COMPONENT TREE" in result
    assert "STATE OWNERSHIP MAP" in result
    assert "UI TO API CALL MAP" in result
