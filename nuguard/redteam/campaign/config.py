"""Validated configuration for ``redteam.mode: campaign``.

Mapped from the nested ``redteam.campaign:`` block in ``nuguard.yaml`` (flattened
to ``redteam_campaign_*`` keys by :mod:`nuguard.config`) and carried on
:class:`nuguard.redteam.public_api.RedteamRunRequest`.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CampaignConfig(BaseModel):
    """Bounds and policy knobs for a campaign run.

    Run-level budgets default to ``None`` (no hidden cap); per-objective and
    per-branch limits are always bounded.

    Example:
        >>> CampaignConfig(max_turns_per_branch=12).max_turns_per_branch
        12
    """

    model_config = ConfigDict(extra="forbid")

    max_turns_per_branch: int = Field(
        default=24, ge=1, le=200,
        description="Rotate a conversation branch after this many turns.",
    )
    max_branch_tokens: int = Field(
        default=8000, ge=500,
        description="Rotate a conversation branch after this many estimated tokens.",
    )
    max_objective_turns: int = Field(
        default=12, ge=1, le=100,
        description="Maximum turns spent on a single objective.",
    )
    max_retries_per_incident: int = Field(
        default=2, ge=0, le=10,
        description="Retries allowed per transport incident within the retry window.",
    )
    retry_window_seconds: float = Field(
        default=120.0, gt=0.0,
        description="Window over which max_retries_per_incident is counted.",
    )
    confirm_in_fresh_sessions: bool = Field(
        default=True,
        description="Reproduce candidate findings in a fresh conversation.",
    )
    target_supports_session_reset: bool | None = Field(
        default=None,
        description=(
            "Operator claim about the target: True = a fresh session can be created, "
            "False = no reset/isolation possible, None = detect."
        ),
    )
    campaign_warmup: bool = Field(
        default=True,
        description="Run one benign warm-up per compatible branch (campaign-managed).",
    )
    confirmation_reserve_fraction: float = Field(
        default=0.2, ge=0.0, le=0.9,
        description="Share of a finite run budget reserved for confirmation/recovery.",
    )
    max_run_target_requests: int | None = Field(
        default=None, ge=1, description="Run-wide target request budget (None = unlimited).",
    )
    max_run_seconds: float | None = Field(
        default=None, gt=0.0, description="Run-wide wall-clock budget (None = unlimited).",
    )
    max_run_llm_cost_usd: float | None = Field(
        default=None, gt=0.0, description="Run-wide attacker-LLM cost budget (None = unlimited).",
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> "CampaignConfig":
        if self.confirm_in_fresh_sessions and self.target_supports_session_reset is False:
            raise ValueError(
                "redteam.campaign.confirm_in_fresh_sessions=true conflicts with "
                "target_supports_session_reset=false: a target that cannot reset a "
                "session cannot confirm findings in a fresh one."
            )
        if self.max_objective_turns > self.max_turns_per_branch:
            raise ValueError(
                "redteam.campaign.max_objective_turns must be <= max_turns_per_branch"
            )
        return self

    @classmethod
    def from_flat(cls, flat: dict[str, Any]) -> "CampaignConfig":
        """Build from flat ``redteam_campaign_*`` keys (missing keys use defaults)."""
        prefix = "redteam_campaign_"
        return cls(**{k[len(prefix):]: v for k, v in flat.items() if k.startswith(prefix)})
