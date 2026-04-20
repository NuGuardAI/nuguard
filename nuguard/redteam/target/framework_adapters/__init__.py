"""Framework-specific protocol adapters for TargetAppClient.

Each adapter handles the unique request/response protocol of a particular AI
framework (e.g. Google ADK) so that :class:`TargetAppClient` can remain
framework-agnostic.

The :class:`FrameworkAdapter` protocol defines the interface every adapter must
implement.  Use :func:`make_framework_adapter` to auto-detect the correct
adapter from an AI-SBOM.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import httpx

from .factory import make_framework_adapter
from .google_adk import GoogleADKAdapter
from .google_ces import GoogleCESAdapter

__all__ = ["FrameworkAdapter", "GoogleADKAdapter", "GoogleCESAdapter", "make_framework_adapter"]


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Protocol for framework-specific HTTP adapters.

    Each adapter is responsible for:

    1. **session lifecycle** – creating (and optionally recycling) server-side
       sessions before the first message is sent.
    2. **request body construction** – building the exact JSON body that the
       framework expects.
    3. **response parsing** – extracting plain text and tool-call records from
       the (potentially nested / list-shaped) response body.
    """

    async def ensure_session(
        self, http_client: "httpx.AsyncClient", scenario_key: str = ""
    ) -> str:
        """Create or return a cached session ID.

        Args:
            http_client: An already-configured :class:`httpx.AsyncClient` with
                the target base URL and auth headers already applied.
            scenario_key: Unique identifier for the current scenario so that
                concurrent scenarios can each maintain their own server-side
                session.

        Returns:
            A non-empty session identifier string.
        """
        ...

    def build_body(self, text: str, session_id: str) -> dict[str, Any]:
        """Build the POST body for a single-turn message.

        Args:
            text: The plain-text message to send.
            session_id: The session ID returned by :meth:`ensure_session`.

        Returns:
            A JSON-serialisable dict ready to be POSTed to the framework's
            chat/run endpoint.
        """
        ...

    def extract_text(self, data: list[Any] | dict[str, Any]) -> str:
        """Extract plain-text response from the framework's response body.

        Args:
            data: Parsed JSON response from the framework (list or dict).

        Returns:
            The assistant's text response, or an empty string when not found.
        """
        ...

    def extract_tool_calls(self, data: list[Any] | dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool/function-call records from the response body.

        Args:
            data: Parsed JSON response from the framework (list or dict).

        Returns:
            A list of dicts, each representing a single tool invocation.
        """
        ...

    @property
    def run_path(self) -> str:
        """The HTTP path used to submit messages to the framework endpoint."""
        ...

    def reset_session(self, scenario_key: str = "") -> None:
        """Discard the cached session so the next call creates a fresh one.

        Args:
            scenario_key: The scenario key to clear, matching the one passed to
                :meth:`ensure_session`.
        """
        ...
