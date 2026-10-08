"""Config schema for the ``target.browser_discovery`` nuguard.yaml override block.

Shared settings apply to target verification and browser fallback in behavior
and redteam. Capability-specific browser_discovery fields override shared
values. The explicit discover-browser command reads this block directly.
Settings do not activate browser fallback or require Playwright at import time.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BrowserDiscoveryConfig(BaseModel):
    """Optional per-app overrides for the generic browser-login heuristics.

    Empty selector fields use generic heuristics. Configured selectors are
    tried first. chat_ui_timeout_ms bounds widget opening and input readiness,
    independently of navigation and outgoing-request capture timeouts.
    """

    login_button_text: list[str] = Field(default_factory=list)
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    post_login_wait_selector: str = ""
    identity_endpoint: str = ""
    chat_input_selector: str = ""
    chat_opener_selector: str = ""
    chat_ui_timeout_ms: int = Field(default=10000, ge=1, le=60000)
    send_button_selector: str = ""
    extra_wait_ms: int = 500
    navigation_timeout_ms: int = 30000

    @classmethod
    def from_target_block(cls, target_block: dict | None) -> "BrowserDiscoveryConfig":
        """Build from the raw ``target:`` mapping of a parsed nuguard.yaml.

        Missing/non-dict ``browser_discovery`` keys resolve to all-defaults,
        matching the "no config, use heuristics" default already documented.
        """
        raw = (target_block or {}).get("browser_discovery")
        if not isinstance(raw, dict):
            return cls()
        return cls(**raw)
