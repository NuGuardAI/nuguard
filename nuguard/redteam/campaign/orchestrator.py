"""CampaignOrchestrator entry point (implemented incrementally; see v5 plan)."""
from __future__ import annotations

from typing import Any


class CampaignModeUnavailableError(NotImplementedError):
    """Raised until the campaign engine lands, so ``mode: campaign`` never silently
    falls back to the legacy engine."""


class CampaignOrchestrator:
    """Same ``run()`` contract as :class:`RedteamOrchestrator` (engine pending)."""

    def __init__(self, **_: Any) -> None:
        raise CampaignModeUnavailableError(
            "redteam.mode 'campaign' is not implemented yet in this build."
        )
