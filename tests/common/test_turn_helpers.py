"""Unit tests for nuguard.common.turn_helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nuguard.common.turn_helpers import (
    handle_confirmation_request,
    handle_credential_request,
    handle_mid_turn_interrupts,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CREDS = {"password": "secret123", "username": "admin"}


def _make_client(response: str = "ok", calls: list | None = None) -> MagicMock:
    client = MagicMock()
    client.send = AsyncMock(return_value=(response, calls or []))
    return client


def _make_session() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# handle_credential_request
# ---------------------------------------------------------------------------


class TestHandleCredentialRequest:
    @pytest.mark.asyncio
    async def test_empty_response_returns_original(self) -> None:
        client = _make_client()
        session = _make_session()
        resp, calls, ok = await handle_credential_request(
            client=client, session=session, response="", credentials=CREDS
        )
        assert resp == ""
        assert calls == []
        assert ok is False
        client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_credentials_returns_original(self) -> None:
        client = _make_client()
        session = _make_session()
        resp, calls, ok = await handle_credential_request(
            client=client, session=session,
            response="Please enter your password.",
            credentials={},
        )
        assert resp == "Please enter your password."
        assert ok is False
        client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_credential_request_in_response(self) -> None:
        client = _make_client()
        session = _make_session()
        resp, calls, ok = await handle_credential_request(
            client=client, session=session,
            response="Here is your answer.",
            credentials=CREDS,
        )
        assert resp == "Here is your answer."
        assert ok is False
        client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_supplies_credential_and_returns_new_response(self) -> None:
        client = _make_client(response="Login successful!", calls=["tool_a"])
        session = _make_session()

        with (
            patch("nuguard.common.credentials.detect_credential_request", return_value="password"),
            patch("nuguard.common.credentials.get_credential", return_value="secret"),
            patch("nuguard.common.credentials.credential_reply", return_value="My password is secret"),
            patch("nuguard.common.credentials.detect_login_success", return_value=True),
        ):
            resp, calls, ok = await handle_credential_request(
                client=client, session=session,
                response="Please enter your password.",
                credentials=CREDS,
            )

        assert resp == "Login successful!"
        assert calls == ["tool_a"]
        assert ok is True
        client.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_failure_returns_original(self) -> None:
        client = MagicMock()
        client.send = AsyncMock(side_effect=RuntimeError("connection error"))
        session = _make_session()

        with (
            patch("nuguard.common.credentials.detect_credential_request", return_value="password"),
            patch("nuguard.common.credentials.get_credential", return_value="s"),
            patch("nuguard.common.credentials.credential_reply", return_value="s"),
        ):
            resp, calls, ok = await handle_credential_request(
                client=client, session=session,
                response="Please enter your password.",
                credentials=CREDS,
            )

        assert resp == "Please enter your password."
        assert calls == []
        assert ok is False


# ---------------------------------------------------------------------------
# handle_confirmation_request
# ---------------------------------------------------------------------------


class TestHandleConfirmationRequest:
    @pytest.mark.asyncio
    async def test_empty_response_returns_original(self) -> None:
        client = _make_client()
        session = _make_session()
        resp, calls = await handle_confirmation_request(
            client=client, session=session, response="", original_message="do thing"
        )
        assert resp == ""
        assert calls == []
        client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_confirmation_in_response(self) -> None:
        client = _make_client()
        session = _make_session()
        with patch("nuguard.common.credentials.detect_confirmation_request", return_value=None):
            resp, calls = await handle_confirmation_request(
                client=client, session=session,
                response="Sure, I will do that.",
                original_message="do thing",
            )
        assert resp == "Sure, I will do that."
        assert calls == []
        client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_replies_to_confirmation_request(self) -> None:
        client = _make_client(response="Confirmed, proceeding.", calls=["tool_b"])
        session = _make_session()

        with (
            patch("nuguard.common.credentials.detect_confirmation_request", return_value="confirm"),
            patch(
                "nuguard.common.credentials.generate_contextual_reply",
                new_callable=AsyncMock,
                return_value="Yes, please proceed.",
            ),
        ):
            resp, calls = await handle_confirmation_request(
                client=client, session=session,
                response="Are you sure you want to proceed?",
                original_message="delete all records",
            )

        assert resp == "Confirmed, proceeding."
        assert calls == ["tool_b"]
        client.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_failure_returns_original(self) -> None:
        client = MagicMock()
        client.send = AsyncMock(side_effect=RuntimeError("timeout"))
        session = _make_session()

        with (
            patch("nuguard.common.credentials.detect_confirmation_request", return_value="confirm"),
            patch(
                "nuguard.common.credentials.generate_contextual_reply",
                new_callable=AsyncMock,
                return_value="yes",
            ),
        ):
            resp, calls = await handle_confirmation_request(
                client=client, session=session,
                response="Are you sure?",
                original_message="delete records",
            )

        assert resp == "Are you sure?"
        assert calls == []


# ---------------------------------------------------------------------------
# handle_mid_turn_interrupts
# ---------------------------------------------------------------------------


class TestHandleMidTurnInterrupts:
    @pytest.mark.asyncio
    async def test_no_interrupts_returns_original(self) -> None:
        client = _make_client()
        session = _make_session()

        with (
            patch("nuguard.common.credentials.detect_credential_request", return_value=None),
            patch("nuguard.common.credentials.detect_confirmation_request", return_value=None),
        ):
            resp, calls = await handle_mid_turn_interrupts(
                client=client, session=session,
                response="Here is your answer.",
                original_message="tell me something",
                tool_calls=["existing_call"],
                credentials=CREDS,
            )

        assert resp == "Here is your answer."
        assert calls == ["existing_call"]
        client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_credentials_skips_credential_check(self) -> None:
        client = _make_client()
        session = _make_session()

        with patch("nuguard.common.credentials.detect_confirmation_request", return_value=None):
            resp, calls = await handle_mid_turn_interrupts(
                client=client, session=session,
                response="Please enter your password.",
                original_message="do thing",
                credentials={},
            )

        # Without credentials, credential detection should not be triggered
        assert resp == "Please enter your password."
        client.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_credential_request_updates_response(self) -> None:
        client = _make_client(response="Login ok.", calls=["cred_call"])
        session = _make_session()

        with (
            patch("nuguard.common.credentials.detect_credential_request", return_value="password"),
            patch("nuguard.common.credentials.get_credential", return_value="pw"),
            patch("nuguard.common.credentials.credential_reply", return_value="pw"),
            patch("nuguard.common.credentials.detect_login_success", return_value=True),
            patch("nuguard.common.credentials.detect_confirmation_request", return_value=None),
        ):
            resp, calls = await handle_mid_turn_interrupts(
                client=client, session=session,
                response="Enter password.",
                original_message="do thing",
                tool_calls=["original_call"],
                credentials=CREDS,
            )

        assert resp == "Login ok."
        # Original call + cred_call from the credential step
        assert "original_call" in calls
        assert "cred_call" in calls

    @pytest.mark.asyncio
    async def test_tool_calls_none_treated_as_empty(self) -> None:
        client = _make_client()
        session = _make_session()

        with (
            patch("nuguard.common.credentials.detect_credential_request", return_value=None),
            patch("nuguard.common.credentials.detect_confirmation_request", return_value=None),
        ):
            resp, calls = await handle_mid_turn_interrupts(
                client=client, session=session,
                response="ok",
                original_message="hi",
                tool_calls=None,
                credentials=None,
            )

        assert resp == "ok"
        assert calls == []
