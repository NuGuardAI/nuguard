"""Optional browser-based endpoint detection adapter."""

from __future__ import annotations

from nuguard.common.endpoint_detection.models import EndpointSource, PayloadShape


async def detect_with_browser(
    target_url: str,
    *,
    chat_message: str = "Hello",
    timeout_s: int = 30,
) -> tuple[str, PayloadShape] | None:
    """Observe the target UI's chat request as a last-resort detector.

    The browser module is imported lazily so users who do not use this fallback
    do not need Playwright or a browser runtime merely to import the package.
    """
    from nuguard.common.browser_login.session import sniff_chat_endpoint_headless

    result = await sniff_chat_endpoint_headless(
        target_url,
        chat_message=chat_message,
        timeout_s=timeout_s,
    )
    if result is None:
        return None
    path, payload_key, payload_list = result
    return path, PayloadShape(
        key=payload_key,
        is_list=payload_list,
        source=EndpointSource.BROWSER,
        notes=("Payload shape observed from a browser chat request.",),
    )
