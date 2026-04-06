"""Diagram rendering helpers used by pipeline stages."""

from codedoc.diagrams.c4 import (
    WRITE_C4_ARTIFACT_ANTHROPIC,
    WRITE_C4_ARTIFACT_OPENAI,
    write_c4_artifact,
)

__all__ = [
    "WRITE_C4_ARTIFACT_ANTHROPIC",
    "WRITE_C4_ARTIFACT_OPENAI",
    "write_c4_artifact",
]
