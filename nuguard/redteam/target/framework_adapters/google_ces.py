"""Google Customer Engagement Suite (CES) framework adapter.

CES exposes a ``runSession`` REST API authenticated with a gcloud Bearer token.
This adapter implements the :class:`FrameworkAdapter` protocol so that the
behavior runner and red-team executor can interact with CES-based apps without
per-framework branching in the shared client.

Detecting CES
-------------
An AI-SBOM produced by NuGuard records ``"google-ces"`` in
``summary.frameworks`` when Google CES configuration is detected in the source.
Pass the SBOM to
:func:`~nuguard.redteam.target.framework_adapters.factory.make_framework_adapter`
to get a fully initialised :class:`GoogleCESAdapter`, or ``None`` when CES is
not present.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from nuguard.common.ces_client import (
    CESAuthError,
    CESClient,
    CESDeploymentConfig,
    get_gcloud_token,
)

_log = logging.getLogger(__name__)

# SBOM framework name that indicates a CES-based application.
CES_FRAMEWORK_NAME = "google-ces"


class GoogleCESAdapter:
    """Framework adapter for Google Customer Engagement Suite (CES) applications.

    Instead of the ADK ``/run`` + session REST API, this adapter calls the CES
    ``runSession`` API directly using a gcloud Bearer token.

    Detected from SBOM when ``summary.frameworks`` contains ``"google-ces"`` or
    any node has ``metadata.framework == "google-ces"``.
    """

    def __init__(self, ces_config: CESDeploymentConfig) -> None:
        self._config = ces_config
        self._client = CESClient(ces_config, token_fn=get_gcloud_token)
        # Cached session IDs keyed by scenario_key
        self._session_ids: dict[str, str] = {}

    # ------------------------------------------------------------------
    # FrameworkAdapter protocol
    # ------------------------------------------------------------------

    @property
    def run_path(self) -> str:
        """CES does not use a local run path; return empty string."""
        return ""

    async def ensure_session(
        self,
        http_client: httpx.AsyncClient,
        scenario_key: str = "",
    ) -> str:
        """Return a session ID, generating a new one per scenario_key.

        Args:
            http_client: Not used for CES (calls go directly to ces.googleapis.com).
            scenario_key: Unique identifier for the current scenario.

        Returns:
            A non-empty session identifier string.
        """
        if scenario_key not in self._session_ids:
            session_id = self._client.new_session_id()
            self._session_ids[scenario_key] = session_id
            _log.info(
                "GoogleCESAdapter: created session %s for scenario_key=%r",
                session_id,
                scenario_key,
            )
        return self._session_ids[scenario_key]

    def reset_session(
        self,
        scenario_key: str = "",
    ) -> None:
        """Clear the cached session ID so the next ensure_session call gets a fresh one.

        Called synchronously by the runner between scenarios.  The new session ID
        is generated lazily on the next :meth:`ensure_session` call.

        Args:
            scenario_key: The scenario key to reset.
        """
        self._session_ids.pop(scenario_key, None)

    def build_body(self, text: str, session_id: str) -> dict[str, Any]:
        """Build the CES ``runSession`` request body.

        Args:
            text: The user message text.
            session_id: Active CES session identifier.

        Returns:
            A dict matching the CES ``runSession`` request schema.
        """
        return {
            "config": {
                "session": self._config.session_resource(session_id),
                "app_version": self._config.version_resource(),
                "deployment": self._config.deployment_resource(),
            },
            "inputs": [{"text": text}],
        }

    async def send(
        self,
        http_client: httpx.AsyncClient,
        text: str,
        session_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Call CES ``runSession`` directly (bypasses ``http_client``'s base URL).

        Args:
            http_client: Not used — CES calls are made directly to ces.googleapis.com.
            text: The user message.
            session_id: Active session identifier.

        Returns:
            ``(reply_text, tool_calls)`` — tool_calls is always empty for CES
            since the API does not expose sub-agent event streams.

        Raises:
            CESAuthError: On HTTP 401 or 403.
        """
        try:
            reply = self._client.run_turn(session_id, text)
        except CESAuthError:
            raise
        except Exception as exc:
            _log.error("GoogleCESAdapter.send: unexpected error: %s", exc)
            raise
        return reply, []

    def extract_text(self, data: dict[str, Any] | list[Any]) -> str:
        """Extract text from a CES ``runSession`` response dict.

        Args:
            data: Parsed CES response body.

        Returns:
            The agent reply text, or an empty string.
        """
        if isinstance(data, dict):
            outputs = data.get("outputs", [])
            if outputs and isinstance(outputs, list):
                first = outputs[0]
                if isinstance(first, dict):
                    return str(first.get("text", ""))
        return ""

    def extract_tool_calls(self, data: list[Any] | dict[str, Any]) -> list[dict[str, Any]]:
        """CES does not expose sub-agent tool calls; always returns empty list."""
        return []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def ces_config(self) -> CESDeploymentConfig:
        """The CES deployment configuration."""
        return self._config
