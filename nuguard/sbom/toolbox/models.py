from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    status: str
    tool: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
