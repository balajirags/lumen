"""FastMCP server exposing the ReverseEngineerToolkit tool surface."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from codedoc.kg_tools import KuzuBackend, ReverseEngineerToolkit


def _pipeline_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _python_type(param_type: str) -> Any:
    return {
        "integer": int,
        "boolean": bool,
        "number": float,
    }.get(param_type, str)


def build_mcp_server(
    db_path: str,
    repo_path: str = "",
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
) -> FastMCP:
    backend = KuzuBackend(db_path)
    toolkit = ReverseEngineerToolkit(backend, repo_path=repo_path)
    server = FastMCP(
        "lumen",
        instructions=(
            "Lumen MCP server for exploring a code property graph stored in KuzuDB. "
            "Use the higher-level repo analysis tools first; use generic query only as an escape hatch."
        ),
        host=host,
        port=port,
        streamable_http_path=path,
    )

    @server.tool(name="help", description="List the available MCP tools and what they do.")
    def help_tool() -> str:
        lines: list[str] = []
        for tool in toolkit.registry.list_tools():
            params = ", ".join(tool.parameters.keys()) or "no params"
            lines.append(f"- {tool.name}({params})")
        return "\n".join(lines)

    for tool in toolkit.registry.list_tools():
        params = []
        for name, meta in tool.parameters.items():
            default = meta.get("default", inspect._empty)
            params.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=default,
                    annotation=_python_type(str(meta.get("type", "string"))),
                )
            )
        signature = inspect.Signature(parameters=params, return_annotation=str)

        def _make_wrapper(tool_name: str):
            def _wrapper(**kwargs):
                return toolkit.call(tool_name, **kwargs)

            _wrapper.__name__ = tool_name
            _wrapper.__doc__ = toolkit.registry.get(tool_name).description if toolkit.registry.get(tool_name) else ""
            _wrapper.__signature__ = signature
            return _wrapper

        server.add_tool(
            _make_wrapper(tool.name),
            name=tool.name,
            description=tool.description,
            structured_output=False,
        )

    return server


def format_mcp_command(db_path: str, repo_path: str = "") -> str:
    cmd = f"lumen mcp --db-path {db_path}"
    if repo_path:
        cmd += f" --repo-path {repo_path}"
    return cmd


def format_mcp_http_url(*, host: str, port: int, path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    return f"http://{host}:{port}{normalized}"


def format_mcp_http_command(
    db_path: str,
    *,
    repo_path: str = "",
    host: str = "127.0.0.1",
    port: int = 8765,
    path: str = "/mcp",
) -> str:
    cmd = f"lumen mcp-http --db-path {db_path} --host {host} --port {port} --path {path}"
    if repo_path:
        cmd += f" --repo-path {repo_path}"
    return cmd


def format_mcp_http_docker_command(
    db_path: str,
    *,
    repo_path: str = "",
    port: int = 8765,
    path: str = "/mcp",
) -> str:
    parts = [
        "docker run --rm -p {port}:{port} -v {db}:{db}:ro".format(port=port, db=db_path),
    ]
    if repo_path:
        parts.append(f"-v {repo_path}:{repo_path}:ro")
    parts.append(
        "lumen mcp-http --db-path {db} --host 0.0.0.0 --port {port} --path {path}".format(
            db=db_path,
            port=port,
            path=path,
        )
    )
    if repo_path:
        parts[-1] += f" --repo-path {repo_path}"
    return " ".join(parts)


def format_client_snippets(db_path: str, repo_path: str = "") -> dict[str, str]:
    pipeline_dir = str(_pipeline_dir())
    uv_args = ["--directory", pipeline_dir, "run", "lumen", "mcp", "--db-path", db_path]
    global_args = ["mcp", "--db-path", db_path]
    docker_args = [
        "run",
        "--rm",
        "-i",
        "-v",
        f"{db_path}:{db_path}:ro",
    ]
    if repo_path:
        uv_args.extend(["--repo-path", repo_path])
        global_args.extend(["--repo-path", repo_path])
        docker_args.extend(["-v", f"{repo_path}:{repo_path}:ro"])
    docker_args.extend(
        [
            "lumen",
            "mcp",
            "--db-path",
            db_path,
        ]
    )
    if repo_path:
        docker_args.extend(["--repo-path", repo_path])

    uv_args_json = ", ".join(f"\"{arg}\"" for arg in uv_args)
    global_args_json = ", ".join(f"\"{arg}\"" for arg in global_args)
    docker_args_json = ", ".join(f"\"{arg}\"" for arg in docker_args)
    return {
        "VS Code / Claude Desktop (uv)": (
            "{\n"
            "  \"mcpServers\": {\n"
            "    \"lumen\": {\n"
            "      \"command\": \"uv\",\n"
            f"      \"args\": [{uv_args_json}]\n"
            "    }\n"
            "  }\n"
            "}"
        ),
        "Global PATH Install": (
            "{\n"
            "  \"name\": \"lumen\",\n"
            "  \"command\": \"lumen\",\n"
            f"  \"args\": [{global_args_json}]\n"
            "}"
        ),
        "Docker": (
            "{\n"
            "  \"name\": \"lumen\",\n"
            "  \"command\": \"docker\",\n"
            f"  \"args\": [{docker_args_json}]\n"
            "}"
        ),
    }


def format_http_client_snippets(url: str) -> dict[str, str]:
    return {
        "VS Code / Claude Desktop (HTTP)": (
            "{\n"
            "  \"mcpServers\": {\n"
            "    \"lumen\": {\n"
            "      \"type\": \"http\",\n"
            f"      \"url\": \"{url}\"\n"
            "    }\n"
            "  }\n"
            "}"
        ),
        "Cursor / Generic MCP (HTTP)": (
            "{\n"
            "  \"name\": \"lumen\",\n"
            "  \"type\": \"http\",\n"
            f"  \"url\": \"{url}\"\n"
            "}"
        ),
    }


def serve_mcp(
    db_path: str,
    repo_path: str = "",
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
) -> None:
    server = build_mcp_server(
        db_path,
        repo_path=repo_path,
        host=host,
        port=port,
        path=path,
    )
    server.run(transport=transport)
