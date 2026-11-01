from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nuguard.common.auth import AuthConfig
from nuguard.common.endpoint_detection.browser import detect_with_browser
from nuguard.common.endpoint_detection.models import EndpointSource


@pytest.mark.asyncio
async def test_authenticated_browser_fallback_extracts_observed_path_and_payload() -> None:
    auth = AuthConfig(type="basic", username="alice", password="secret")
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    session.run.return_value = SimpleNamespace(
        sniffed_endpoint="https://app.test/api/conversations",
        sniffed_chat_request={"prompt": "Hello", "consumerID": "c1"},
    )

    with patch(
        "nuguard.common.browser_login.session.BrowserLoginSession",
        return_value=session,
    ) as browser_session:
        result = await detect_with_browser("https://app.test", auth_config=auth)

    browser_session.assert_called_once()
    session.run.assert_awaited_once_with(chat_message="Hello", sniff_chat=True)
    assert result is not None
    path, payload = result
    assert path == "/api/conversations"
    assert payload.key == "prompt"
    assert payload.source is EndpointSource.BROWSER