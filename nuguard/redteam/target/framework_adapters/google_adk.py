"""Google Agent Development Kit (ADK) framework adapter.

ADK exposes a REST API with a specific protocol:

  * **Session creation**: ``POST /apps/{app_name}/users/{user_id}/sessions``
    → ``{"id": "<session_id>"}``
  * **Message submission**: ``POST /run``
    body: ``RunAgentRequest`` =
    ``{"appName": str, "userId": str, "sessionId": str,
       "newMessage": {"role": "user", "parts": [{"text": str}]}}``
  * **Response**: a JSON array of ``Event`` objects.
    Text lives at ``event["content"]["parts"][*]["text"]`` for events whose
    ``author`` is not ``"__system__"``.

This module provides :class:`GoogleADKAdapter` which implements the
:class:`~nuguard.redteam.target.framework_adapters.FrameworkAdapter` protocol
so that :class:`~nuguard.redteam.target.client.TargetAppClient` can interact
with ADK-based apps without any per-framework branching in the shared client.

Detecting ADK
-------------
An AI-SBOM produced by NuGuard records ``"google-adk"`` and/or ``"google_adk"``
in ``summary.frameworks`` when Google ADK is detected in the source.  Pass
the SBOM to :func:`~nuguard.redteam.target.framework_adapters.factory.make_framework_adapter`
to get a fully initialised :class:`GoogleADKAdapter`, or ``None`` when ADK is
not present.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# SBOM framework names that indicate an ADK-based application.
ADK_FRAMEWORK_NAMES: frozenset[str] = frozenset({"google-adk", "google_adk"})

_DEFAULT_USER_ID = "nuguard"
_DEFAULT_RUN_PATH = "/run"
_LIST_APPS_PATH = "/list-apps"
_SESSION_PATH_TEMPLATE = "/apps/{app_name}/users/{user_id}/sessions"


class GoogleADKAdapter:
    """Framework adapter for Google Agent Development Kit (ADK) applications.

    The adapter manages the ADK session lifecycle and translates between
    nuguard's generic ``(text, session)`` interface and ADK's
    ``RunAgentRequest`` / ``Event[]`` protocol.

    Args:
        app_name: ADK application name (e.g. ``"marketing_campaign_agent"``).
            When empty, :meth:`resolve_app_name` is called on first use.
        user_id: ADK user identifier injected into every request.
        session_per_scenario: When ``True`` (default) a fresh session is
            created per :meth:`reset_session` call, giving each scenario an
            isolated conversation history.  When ``False`` the session is
            reused across the entire scan run.
        run_path: HTTP path for the ``/run`` endpoint.
        streaming_run_path: HTTP path for the SSE streaming endpoint.
            Defaults to ``"/run_sse"``.  Set to the same value as *run_path*
            to disable SSE even when the SBOM detects streaming support.
    """

    def __init__(
        self,
        app_name: str = "",
        user_id: str = _DEFAULT_USER_ID,
        session_per_scenario: bool = True,
        run_path: str = _DEFAULT_RUN_PATH,
        streaming_run_path: str = "/run_sse",
        sbom_app_candidates: list[str] | None = None,
    ) -> None:
        self._app_name = app_name.strip()
        self._user_id = user_id or _DEFAULT_USER_ID
        self._session_per_scenario = session_per_scenario
        self._run_path = run_path or _DEFAULT_RUN_PATH
        self._streaming_run_path = streaming_run_path or "/run_sse"
        # Ordered list of app-name candidates derived from SBOM agent evidence
        # paths.  Used by _resolve_app_name to prefer the right entry from
        # /list-apps when multiple ADK apps exist in the same source tree.
        self._sbom_app_candidates: list[str] = sbom_app_candidates or []
        # Cached session IDs keyed by scenario_key (usually AttackSession.session_id).
        # Empty-string key is used when called without a scenario context.
        self._session_ids: dict[str, str] = {}
        # Asyncio lock to serialise concurrent session-creation calls for the
        # same scenario key (avoid duplicate POST requests).
        import asyncio as _asyncio
        self._session_lock: "_asyncio.Lock" = _asyncio.Lock()

    # ------------------------------------------------------------------
    # FrameworkAdapter protocol
    # ------------------------------------------------------------------

    @property
    def run_path(self) -> str:
        """The HTTP path used to submit messages (``/run`` by default)."""
        return self._run_path

    @property
    def streaming_run_path(self) -> str:
        """The HTTP path for SSE streaming output (``/run_sse`` by default)."""
        return self._streaming_run_path

    async def ensure_session(
        self,
        http_client: httpx.AsyncClient,
        scenario_key: str = "",
    ) -> str:
        """Return a valid session ID, creating one if needed.

        On the first call for a given ``scenario_key`` (or after
        :meth:`reset_session`) posts to
        ``/apps/{app_name}/users/{user_id}/sessions`` to obtain a server-side
        session, then caches the ID for subsequent turns within the same
        scenario.

        When ``session_per_scenario=False`` all calls share a single session
        regardless of ``scenario_key``.

        Args:
            http_client: An :class:`httpx.AsyncClient` already pointed at the
                target base URL with auth headers applied.
            scenario_key: Unique identifier for the current scenario (typically
                :attr:`~nuguard.redteam.target.session.AttackSession.session_id`).
                Different concurrent scenarios each look up their own session.

        Returns:
            A non-empty session ID string.

        Raises:
            RuntimeError: When ``app_name`` is still empty after attempting
                auto-resolution, or when the session endpoint returns a
                non-2xx status.
        """
        # When session_per_scenario is disabled, use a single shared key.
        cache_key = "" if not self._session_per_scenario else scenario_key

        if cache_key in self._session_ids:
            return self._session_ids[cache_key]

        async with self._session_lock:
            # Double-check inside the lock (another coroutine may have created it).
            if cache_key in self._session_ids:
                return self._session_ids[cache_key]

            # Resolve app_name lazily if not already set.
            if not self._app_name:
                await self._resolve_app_name(http_client)

            if not self._app_name:
                raise RuntimeError(
                    "GoogleADKAdapter: app_name could not be determined. "
                    "Set adk.app_name in nuguard.yaml or ensure the target exposes "
                    "/list-apps."
                )

            session_path = _SESSION_PATH_TEMPLATE.format(
                app_name=self._app_name, user_id=self._user_id
            )
            try:
                resp = await http_client.post(session_path, json={})
                resp.raise_for_status()
                body = resp.json()
                session_id: str = body.get("id") or body.get("session_id") or ""
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"GoogleADKAdapter: session creation failed "
                    f"(HTTP {exc.response.status_code}) at {session_path}: "
                    f"{exc.response.text[:200]}"
                ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"GoogleADKAdapter: session creation request error at {session_path}: {exc}"
                ) from exc

            if not session_id:
                # Fallback: generate a client-side UUID.  Some ADK versions accept
                # caller-supplied session IDs; if they don't the /run call will fail
                # with a helpful error message.
                session_id = str(uuid.uuid4())
                _log.warning(
                    "GoogleADKAdapter: session endpoint returned no 'id' field — "
                    "using generated session_id=%s", session_id,
                )

            self._session_ids[cache_key] = session_id
            _log.info(
                "GoogleADKAdapter: created session %s for app=%s user=%s scenario_key=%r",
                session_id, self._app_name, self._user_id, cache_key,
            )
            return session_id

    def build_body(self, text: str, session_id: str) -> dict[str, Any]:
        """Build the ``RunAgentRequest`` JSON body for ADK's ``/run`` endpoint.

        Args:
            text: The plain-text message to deliver to the agent.
            session_id: Active ADK session identifier.

        Returns:
            A dict matching the ``RunAgentRequest`` schema expected by ADK.
        """
        return {
            "app_name": self._app_name,
            "user_id": self._user_id,
            "session_id": session_id,
            "new_message": {
                "role": "user",
                "parts": [{"text": text}],
            },
        }

    def extract_text(self, data: list[Any] | dict[str, Any]) -> str:
        """Extract the agent's reply text from an ADK ``Event[]`` response.

        ADK returns a JSON array of events.  This method collects text parts
        from all non-system events that carry ``content.parts[*].text`` and
        returns them as a single string (newline-joined for multi-part replies).

        Args:
            data: Parsed response body — expected to be a list of ADK events.

        Returns:
            The assistant's text, or an empty string when not found.
        """
        if not isinstance(data, list):
            _log.debug(
                "GoogleADKAdapter.extract_text: expected list, got %s", type(data).__name__
            )
            return ""

        text_parts: list[str] = []
        for event in data:
            if not isinstance(event, dict):
                continue
            # Skip system / infrastructure events
            author = event.get("author", "")
            if author == "__system__":
                continue

            content = event.get("content")
            if not isinstance(content, dict):
                continue

            parts = content.get("parts", [])
            if not isinstance(parts, list):
                continue

            for part in parts:
                if not isinstance(part, dict):
                    continue
                # Only plain text parts — skip function calls/responses
                if "function_call" in part or "function_response" in part:
                    continue
                text = part.get("text", "")
                if text and isinstance(text, str):
                    text_parts.append(text.strip())

        return "\n".join(t for t in text_parts if t)

    def extract_tool_calls(self, data: list[Any] | dict[str, Any]) -> list[dict[str, Any]]:
        """Extract ``functionCall`` records from an ADK ``Event[]`` response.

        Args:
            data: Parsed response body — expected to be a list of ADK events.

        Returns:
            A list of dicts, each with ``name`` and ``args`` keys corresponding
            to ADK ``FunctionCall`` objects.
        """
        if not isinstance(data, list):
            return []

        calls: list[dict[str, Any]] = []
        for event in data:
            if not isinstance(event, dict):
                continue
            content = event.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts", [])
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                fc = part.get("function_call")
                if isinstance(fc, dict):
                    calls.append(
                        {
                            "name": fc.get("name", ""),
                            "args": fc.get("args", {}),
                        }
                    )
        return calls

    def reset_session(self, scenario_key: str = "") -> None:
        """Discard the cached session ID for a given scenario key.

        Call this between scenarios when ``session_per_scenario=True`` to
        ensure each scenario starts with a clean conversation history.

        Args:
            scenario_key: The key used in the corresponding :meth:`ensure_session`
                call.  Pass an empty string to clear the shared session (used
                when ``session_per_scenario=False``).
        """
        cache_key = "" if not self._session_per_scenario else scenario_key
        self._session_ids.pop(cache_key, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_app_name(self, http_client: httpx.AsyncClient) -> None:
        """Attempt to resolve ``app_name`` from the ADK ``/list-apps`` endpoint.

        When ``/list-apps`` returns a list with one entry, that entry is used.
        When multiple entries exist, the list is matched against
        ``self._sbom_app_candidates`` (ordered by AGENT-node frequency in the
        SBOM) to pick the most relevant app.  Falls back to the first entry if
        no candidate matches.

        Args:
            http_client: An :class:`httpx.AsyncClient` pointed at the target.
        """
        try:
            resp = await http_client.get(_LIST_APPS_PATH)
            if resp.status_code != 200:
                _log.debug(
                    "GoogleADKAdapter: /list-apps returned HTTP %d — cannot auto-resolve app_name",
                    resp.status_code,
                )
                return
            apps = resp.json()
            if not isinstance(apps, list) or not apps:
                _log.debug("GoogleADKAdapter: /list-apps returned empty list")
                return

            # Normalise to list of non-empty strings
            app_strs: list[str] = [str(a).strip() for a in apps if a]
            if not app_strs:
                return

            # Fast path: only one app listed
            if len(app_strs) == 1:
                self._app_name = app_strs[0]
                _log.info(
                    "GoogleADKAdapter: auto-resolved app_name=%r from /list-apps (only entry)",
                    self._app_name,
                )
                return

            # Multiple apps: prefer the entry that best matches the SBOM-derived
            # candidates (ordered by AGENT-node frequency).
            if self._sbom_app_candidates:
                app_set = set(app_strs)
                for candidate in self._sbom_app_candidates:
                    if candidate in app_set:
                        self._app_name = candidate
                        _log.info(
                            "GoogleADKAdapter: auto-resolved app_name=%r via SBOM candidate match",
                            self._app_name,
                        )
                        return

            # No SBOM candidates or no match — fall back to first entry with a warning
            self._app_name = app_strs[0]
            _log.warning(
                "GoogleADKAdapter: /list-apps returned %d apps and no SBOM candidates matched; "
                "defaulting to first entry %r.  Set adk.app_name in nuguard.yaml to override.",
                len(app_strs),
                self._app_name,
            )
        except Exception as exc:
            _log.debug("GoogleADKAdapter: /list-apps request failed: %s", exc)

    @property
    def app_name(self) -> str:
        """Currently configured ADK application name."""
        return self._app_name

    @property
    def user_id(self) -> str:
        """ADK user identifier used in every request."""
        return self._user_id

    @property
    def session_per_scenario(self) -> bool:
        """Whether a fresh session is created for each scenario."""
        return self._session_per_scenario
