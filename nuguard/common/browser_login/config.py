"""Config schema for the ``target.browser_discovery`` nuguard.yaml override block.

This model is read directly from raw parsed YAML by
``nuguard/cli/commands/target_browser.py`` — it intentionally does NOT flow
through ``NuGuardConfig``/``_flatten_yaml`` in ``nuguard/config.py``, since it
is consumed by exactly one command (``nuguard target discover-browser``) and
never by ``behavior``/``redteam`` runners. This keeps the blast radius of this
feature small: no new fields on the shared settings model, no new flattening
branch, no new path-rebasing entry needed for this block itself.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BrowserDiscoveryConfig(BaseModel):
    """Optional per-app overrides for the generic browser-login heuristics.

    Every field defaults to empty, which means "fall back to the generic
    heuristics in heuristics.py for this step." A non-empty value is always
    tried first, ahead of the heuristic candidate list.
    """

    login_button_text: list[str] = Field(default_factory=list)
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    post_login_wait_selector: str = ""
    identity_endpoint: str = ""
    chat_input_selector: str = ""
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
