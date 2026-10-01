"""Optional browser-based endpoint detection adapter."""

from __future__ import annotations

from typing import Any

from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape


async def detect_with_browser(
    target_url: str,
    *,
    chat_message: str = "Hello",
    timeout_s: int = 30,
    auth_config: Any = None,
) -> tuple[str, PayloadShape] | None:
    """Observe the target UI's chat request as a last-resort detector.

    The browser module is imported lazily so users who do not use this fallback
    do not need Playwright or a browser runtime merely to import the package.
    """
    path: str | None = None
    payload_key: str | None = None
    payload_list = False
    if auth_config and getattr(auth_config, "username", None) and getattr(auth_config, "password", None):
        from urllib.parse import urlparse  # noqa: PLC0415

        from nuguard.common.browser_login.config import BrowserDiscoveryConfig  # noqa: PLC0415
        from nuguard.common.browser_login.session import (  # noqa: PLC0415
            BrowserLoginSession,
            _find_chat_payload_key,
        )

        try:
            async with BrowserLoginSession(
                target_url,
                auth_config,
                BrowserDiscoveryConfig(),
                headless=True,
                timeout_s=timeout_s,
            ) as session:
                login_result = await session.run(chat_message=chat_message, sniff_chat=True)
            if login_result.sniffed_endpoint and login_result.sniffed_chat_request:
                path = urlparse(login_result.sniffed_endpoint).path or None
                key_info = _find_chat_payload_key(login_result.sniffed_chat_request, chat_message)
                if key_info is not None:
                    payload_key, payload_list = key_info
        except Exception as exc:  # noqa: BLE001 - browser fallback is best effort
            from nuguard.common.logging import get_logger  # noqa: PLC0415

            get_logger(__name__).info("browser endpoint discovery failed: %s", exc)
    else:
        from nuguard.common.browser_login.session import (
            sniff_chat_endpoint_headless,  # noqa: PLC0415
        )

        sniffed_result = await sniff_chat_endpoint_headless(
            target_url,
            chat_message=chat_message,
            timeout_s=timeout_s,
        )
        if sniffed_result is not None:
            path, payload_key, payload_list = sniffed_result
    if not path or not payload_key:
        return None
    return path, PayloadShape(
        key=payload_key,
        is_list=payload_list,
        source=EndpointSource.BROWSER,
        notes=("Payload shape observed from a browser chat request.",),
    )
