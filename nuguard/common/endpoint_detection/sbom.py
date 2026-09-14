"""SBOM-only endpoint and payload discovery adapters."""

from __future__ import annotations

from typing import Any

from nuguard.common.endpoint_probe import (
    discover_chat_candidates_from_sbom as _discover_chat_candidates,
)
from nuguard.common.endpoint_probe import (
    discover_chat_config_from_sbom as _discover_chat_config,
)
from nuguard.common.endpoint_probe import (
    sbom_indicates_websocket as _sbom_indicates_websocket,
)


def discover_chat_candidates(sbom: Any, **kwargs: Any) -> list[Any]:
    """Return SBOM candidates ranked by the existing chat-likelihood scorer."""
    return list(_discover_chat_candidates(sbom, **kwargs))


def discover_chat_config(
    sbom: Any,
    chat_path: str | None = None,
    chat_payload_key: str = "message",
    chat_payload_list: bool = False,
) -> tuple[str | None, str, bool, str | None]:
    """Return the existing SBOM-selected path and payload metadata."""
    return _discover_chat_config(
        sbom,
        chat_path=chat_path or "",
        chat_payload_key=chat_payload_key,
        chat_payload_list=chat_payload_list,
    )


def indicates_websocket(
    sbom: Any,
    chat_path: str | None = None,
    chat_payload_key: str = "message",
) -> bool:
    """Return whether SBOM metadata identifies the selected route as WebSocket."""
    return bool(
        _sbom_indicates_websocket(
            sbom,
            chat_path=chat_path or "",
            chat_payload_key=chat_payload_key,
        )
    )
