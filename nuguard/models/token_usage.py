"""Shared token-usage model for all NuGuard check results."""
from __future__ import annotations

from pydantic import BaseModel, computed_field


class TokenUsage(BaseModel):
    """LLM token consumption for a single check run.

    Embedded in AnalysisResult, BehaviorAnalysisResult, PolicyCheckResult,
    and RedteamOrchestrator so every module surfaces the same fields.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    llm_model: str | None = None

    @computed_field  # type: ignore[misc]
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            llm_model=self.llm_model or other.llm_model,
        )
