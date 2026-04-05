"""Compatibility exports for pipeline entrypoints."""

from codedoc.pipelines.full import run_pipeline
from codedoc.pipelines.mcp_http import run_mcp_http_pipeline
from codedoc.pipelines.mcp import run_mcp_pipeline

__all__ = ["run_pipeline", "run_mcp_pipeline", "run_mcp_http_pipeline"]
