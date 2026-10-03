"""Agent-friendly tool results.

Tools return a compact, structured summary instead of dumping raw output into the
agent's context. Large outputs stay on disk and are referenced by path.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """What every BioHarbor tool returns to the agent."""

    summary: dict[str, Any] = Field(
        default_factory=dict, description="Structured, compact key results."
    )
    message: str = Field("", description="One or two sentences describing the outcome.")
    files: list[str] = Field(
        default_factory=list, description="Workspace paths of full outputs (read on demand)."
    )
    suggestions: list[str] = Field(
        default_factory=list, description="Possible next steps for the agent."
    )
