from __future__ import annotations

import json

from click.testing import CliRunner

from codedoc.cli import main
from codedoc.mcp_server import (
    format_client_snippets,
    format_http_client_snippets,
    format_mcp_command,
    format_mcp_http_command,
    format_mcp_http_url,
    format_server_identity,
)


def test_format_mcp_command_includes_repo_path():
    cmd = format_mcp_command("/tmp/test-db", "/repo/path")
    assert cmd == "lumen mcp --db-path /tmp/test-db --repo-path /repo/path"


def test_format_client_snippets_include_uv_and_docker_options():
    snippets = format_client_snippets("/tmp/test-db", "/repo/path")
    assert "\"command\": \"uv\"" in snippets["VS Code / Claude Desktop (uv)"]
    assert "\"command\": \"docker\"" in snippets["Docker"]
    assert "--db-path" in snippets["Global PATH Install"]
    assert "\"lumen-path\"" in snippets["VS Code / Claude Desktop (uv)"]
    assert "\"name\": \"lumen-path\"" in snippets["Global PATH Install"]


def test_format_mcp_http_url_and_command():
    url = format_mcp_http_url(host="127.0.0.1", port=8765, path="/mcp")
    cmd = format_mcp_http_command(
        "/tmp/test-db",
        repo_path="/repo/path",
        host="127.0.0.1",
        port=8765,
        path="/mcp",
    )
    assert url == "http://127.0.0.1:8765/mcp"
    assert cmd == "lumen mcp-http --db-path /tmp/test-db --host 127.0.0.1 --port 8765 --path /mcp --repo-path /repo/path"


def test_format_mcp_http_url_rewrites_wildcard_host_for_clients():
    url = format_mcp_http_url(host="0.0.0.0", port=8765, path="/mcp")
    assert url == "http://127.0.0.1:8765/mcp"


def test_format_http_client_snippets_use_url():
    snippets = format_http_client_snippets(
        "http://127.0.0.1:8765/mcp",
        "/tmp/admin-frontend-db",
        "/repo/admin-frontend",
    )
    assert "\"type\": \"http\"" in snippets["VS Code / Claude Desktop (HTTP)"]
    assert "http://127.0.0.1:8765/mcp" in snippets["Cursor / Generic MCP (HTTP)"]
    assert "\"lumen-admin-frontend\"" in snippets["VS Code / Claude Desktop (HTTP)"]
    assert "\"name\": \"lumen-admin-frontend\"" in snippets["Cursor / Generic MCP (HTTP)"]


def test_format_server_identity_prefers_repo_path_name():
    identity = format_server_identity("/tmp/inventory-service-db", "/repo/inventory-service")
    assert identity["repo_name"] == "inventory-service"
    assert identity["server_name"] == "lumen-inventory-service"


def test_format_server_identity_falls_back_to_db_name():
    identity = format_server_identity("/tmp/admin-frontend-db")
    assert identity["repo_name"] == "admin-frontend"


def test_format_server_identity_explicit_repo_name_overrides_path():
    identity = format_server_identity("/tmp/repo-db", "/repo", repo_name="inventory-service")
    assert identity["repo_name"] == "inventory-service"
    assert identity["server_name"] == "lumen-inventory-service"


def test_format_http_client_snippets_use_repo_name_override():
    snippets = format_http_client_snippets(
        "http://127.0.0.1:8765/mcp",
        "/tmp/repo-db",
        "/repo",
        repo_name="inventory-service",
    )
    assert "\"lumen-inventory-service\"" in snippets["VS Code / Claude Desktop (HTTP)"]
    assert "\"name\": \"lumen-inventory-service\"" in snippets["Cursor / Generic MCP (HTTP)"]


def test_mcp_cli_requires_repo_or_db():
    runner = CliRunner()
    result = runner.invoke(main, ["mcp"])
    assert result.exit_code != 0
    assert "Provide REPO_PATH" in result.output


def test_mcp_cli_print_config_for_existing_db(tmp_path, monkeypatch):
    db_path = tmp_path / "repo-db"
    db_path.write_text("stub")

    called: dict[str, object] = {"served": False}

    def fake_serve(db_path: str, repo_path: str = "", transport: str = "stdio"):
        called["served"] = True

    monkeypatch.setattr("codedoc.cli.serve_mcp", fake_serve)

    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "--db-path", str(db_path), "--print-config"])

    assert result.exit_code == 0
    assert called["served"] is False
    assert "MCP Ready" in result.output


def test_mcp_http_cli_print_config_for_existing_db(tmp_path, monkeypatch):
    db_path = tmp_path / "repo-db"
    db_path.write_text("stub")

    called: dict[str, object] = {"served": False}

    def fake_serve(
        db_path: str,
        repo_path: str = "",
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        path: str = "/mcp",
    ):
        called["served"] = True

    monkeypatch.setattr("codedoc.cli.serve_mcp", fake_serve)

    runner = CliRunner()
    result = runner.invoke(main, ["mcp-http", "--db-path", str(db_path), "--print-config"])

    assert result.exit_code == 0
    assert called["served"] is False
    assert "MCP HTTP Ready" in result.output
