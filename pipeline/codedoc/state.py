"""Shared pipeline state for the LangGraph graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PipelineState:
    """Mutable state threaded through all three pipeline stages."""

    # --- Inputs ---
    repo_path: str = ""
    output_dir: str = ""
    repo_metrics: dict[str, Any] | None = None

    # --- Stage 1 outputs ---
    kuzu_path: str = ""
    indexed_languages: list[str] = field(default_factory=list)

    # --- Stage 2 outputs ---
    artifacts_dir: str = ""
    prompt_archetype: str = ""

    # --- Stage 3 outputs ---
    site_path: str = ""

    # --- Metadata ---
    status: str = "pending"  # pending | running | done | failed
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""
    events: list[str] = field(default_factory=list)

    # --- Token usage (populated by Stage 2) ---
    input_tokens: int = 0
    output_tokens: int = 0
    tool_uses: int = 0

    # --- Runtime config (not persisted in pipeline.json) ---
    model: str = "claude-sonnet-4-6"
    provider: str = "auto"
    base_url: str = ""
    max_turns: int = 60
    max_context_tokens: int = 120_000
    timeout: int = 300
    repo_size_check: str = "warn"
    verbose: bool = False
    indexer_bin_dir: str = ""
    agent_prompt: str = ""
    build_script: str = ""
    site_dir: str = ""  # override for doc-site location

    def log(self, stage: str, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] [{stage}] {message}"
        self.events.append(line)
        from codedoc import log as _log  # lazy import avoids circular dep at module level
        _log.log_to_console(stage, message, ts)

    def to_pipeline_json(self) -> dict[str, Any]:
        """Return the dict written to pipeline.json."""
        return {
            "repo_path": self.repo_path,
            "repo_metrics": self.repo_metrics,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "error": self.error,
            "kuzu_path": self.kuzu_path,
            "indexed_languages": self.indexed_languages,
            "artifacts_dir": self.artifacts_dir,
            "prompt_archetype": self.prompt_archetype,
            "site_path": self.site_path,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "total": self.input_tokens + self.output_tokens,
            },
            "tool_uses": self.tool_uses,
            "events": self.events,
        }
