"""Unit tests for nuguard.common.credentials."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from nuguard.common.credentials import (
    confirmation_reply,
    credential_reply,
    detect_confirmation_request,
    detect_credential_request,
    detect_login_success,
    generate_contextual_reply,
    get_credential,
)


class TestDetectCredentialRequest:
    def test_detects_password(self) -> None:
        assert detect_credential_request("Please enter your password.") == "password"

    def test_detects_password_passphrase(self) -> None:
        assert detect_credential_request("Please enter your passphrase.") == "password"

    def test_detects_username(self) -> None:
        assert detect_credential_request("Please provide your username.") == "username"

    def test_detects_email_as_username(self) -> None:
        assert detect_credential_request("Please enter your email address.") == "username"

    def test_detects_api_key(self) -> None:
        assert detect_credential_request("Please provide your API key.") == "api_key"

    def test_detects_token(self) -> None:
        assert detect_credential_request("Please provide your access token.") == "token"

    def test_detects_otp(self) -> None:
        assert detect_credential_request("Please enter your OTP.") == "otp"

    def test_detects_otp_verification_code(self) -> None:
        assert detect_credential_request("Please enter the verification code.") == "otp"

    def test_detects_pin(self) -> None:
        assert detect_credential_request("Please enter your PIN.") == "pin"

    def test_empty_string_returns_none(self) -> None:
        assert detect_credential_request("") is None

    def test_no_request_verb_returns_none(self) -> None:
        assert detect_credential_request("Your username is on file.") is None

    def test_password_reset_discussion_suppressed(self) -> None:
        # The agent is describing a reset flow, not requesting a credential
        assert detect_credential_request(
            "You can reset your password by clicking the reset link."
        ) is None

    def test_forgot_password_suppressed(self) -> None:
        assert detect_credential_request(
            "It looks like you forgot your password. Contact customer support."
        ) is None

    def test_returns_earliest_when_multiple_types(self) -> None:
        # "username" keyword appears before "password" in the string
        text = "Please provide your username and then your password."
        result = detect_credential_request(text)
        assert result == "username"

    def test_case_insensitive(self) -> None:
        assert detect_credential_request("PLEASE ENTER YOUR PASSWORD") == "password"


class TestDetectLoginSuccess:
    def test_logged_in(self) -> None:
        assert detect_login_success("I have logged you in.") is True

    def test_authentication_successful(self) -> None:
        assert detect_login_success("Authentication successful!") is True

    def test_welcome_back(self) -> None:
        assert detect_login_success("Welcome back, Alice!") is True

    def test_successfully_authenticated(self) -> None:
        assert detect_login_success("You have been successfully authenticated.") is True

    def test_login_successful(self) -> None:
        assert detect_login_success("Login successful. Redirecting...") is True

    def test_regular_text_returns_false(self) -> None:
        assert detect_login_success("Please enter your password.") is False

    def test_empty_string_returns_false(self) -> None:
        assert detect_login_success("") is False


class TestGetCredential:
    def test_returns_configured_value(self) -> None:
        configured = {"password": "MySecret123"}
        assert get_credential("password", configured) == "MySecret123"

    def test_falls_back_to_default_when_not_configured(self) -> None:
        assert get_credential("password", {}) == "testpass"

    def test_falls_back_to_default_for_username(self) -> None:
        assert get_credential("username", {}) == "testuser"

    def test_falls_back_to_default_for_otp(self) -> None:
        assert get_credential("otp", {}) == "123456"

    def test_falls_back_to_default_for_pin(self) -> None:
        assert get_credential("pin", {}) == "1234"

    def test_falls_back_to_default_for_api_key(self) -> None:
        assert get_credential("api_key", {}) == "test-api-key"

    def test_falls_back_to_default_for_token(self) -> None:
        assert get_credential("token", {}) == "test-token"

    def test_unknown_type_returns_testvalue(self) -> None:
        assert get_credential("unknown_type", {}) == "testvalue"

    def test_configured_overrides_default(self) -> None:
        configured = {"otp": "654321"}
        assert get_credential("otp", configured) == "654321"

    def test_empty_string_in_config_falls_back_to_default(self) -> None:
        # Empty string is falsy — should use default
        configured = {"password": ""}
        assert get_credential("password", configured) == "testpass"


class TestCredentialReply:
    def test_password_reply(self) -> None:
        result = credential_reply("password", "hunter2")
        assert "hunter2" in result

    def test_username_reply(self) -> None:
        result = credential_reply("username", "alice")
        assert "alice" in result
        assert "username" in result.lower()

    def test_api_key_reply(self) -> None:
        result = credential_reply("api_key", "sk-abc123")
        assert "sk-abc123" in result
        assert "api key" in result.lower()

    def test_token_reply(self) -> None:
        result = credential_reply("token", "tok_xyz")
        assert "tok_xyz" in result
        assert "token" in result.lower()

    def test_otp_reply(self) -> None:
        result = credential_reply("otp", "987654")
        assert "987654" in result

    def test_pin_reply(self) -> None:
        result = credential_reply("pin", "4321")
        assert "4321" in result

    def test_unknown_type_returns_value(self) -> None:
        result = credential_reply("unknown", "myvalue")
        assert "myvalue" in result


class TestDetectConfirmationRequest:
    def test_is_that_correct(self) -> None:
        assert detect_confirmation_request("Is that correct?") == "confirmation"

    def test_shall_i_proceed(self) -> None:
        assert detect_confirmation_request("Shall I proceed?") == "confirmation"

    def test_are_you_sure(self) -> None:
        assert detect_confirmation_request("Are you sure you want to continue?") == "confirmation"

    def test_do_you_want_me_to_proceed(self) -> None:
        assert detect_confirmation_request("Do you want me to proceed?") == "confirmation"

    def test_please_confirm(self) -> None:
        assert detect_confirmation_request("Please confirm this action.") == "confirmation"

    def test_should_i_go_ahead(self) -> None:
        assert detect_confirmation_request("Should I go ahead with the transfer?") == "confirmation"

    def test_clarification_provide_details(self) -> None:
        assert detect_confirmation_request(
            "Could you please provide more details?"
        ) == "clarification"

    def test_clarification_specify_account(self) -> None:
        assert detect_confirmation_request(
            "Which account would you like to use?"
        ) == "clarification"

    def test_clarification_whats_your_confirmation_number(self) -> None:
        assert detect_confirmation_request(
            "Sure—what's your new reservation confirmation number (the one "
            "after rebooking)? Once I have that, I'll set your seat to 14F."
        ) == "clarification"

    def test_clarification_which_trip_relates_to(self) -> None:
        assert detect_confirmation_request(
            "What I need from you: tell me which reservation/flight your "
            "baggage relates to."
        ) == "clarification"

    def test_plain_statement_returns_none(self) -> None:
        assert detect_confirmation_request("I have completed the transfer.") is None

    def test_empty_string_returns_none(self) -> None:
        assert detect_confirmation_request("") is None

    def test_question_without_match_returns_none(self) -> None:
        assert detect_confirmation_request("What is the weather today?") is None

    def test_case_insensitive(self) -> None:
        assert detect_confirmation_request("SHALL I PROCEED?") == "confirmation"

    def test_otp_offer_let_me_know(self) -> None:
        # Real-world phrasing that previously slipped past detection: the
        # agent offers to send an OTP and waits for a go-ahead rather than
        # refusing outright — see nuguard/redteam/executor/executor.py's
        # mid-turn interrupt handling.
        assert detect_confirmation_request(
            "I can send a one-time password (OTP) to your registered phone "
            "number for verification. Once you provide the OTP, I will "
            "proceed with the transfer. Let me know if you'd like me to "
            "send the OTP now."
        ) == "confirmation"

    def test_otp_offer_email_variant(self) -> None:
        assert detect_confirmation_request(
            "I will send a one-time password (OTP) to your registered email "
            "address. Once you provide the OTP, I can proceed with the "
            "rectification scenario. Let me know if you'd like me to send "
            "the OTP now."
        ) == "confirmation"

    def test_let_me_know_if_you_would_like_full_form(self) -> None:
        assert detect_confirmation_request(
            "Let me know if you would like me to proceed with the export."
        ) == "confirmation"

    def test_once_you_provide_standalone(self) -> None:
        assert detect_confirmation_request(
            "Once you provide your account number, I can look that up."
        ) == "confirmation"

    def test_let_me_know_when_ready(self) -> None:
        assert detect_confirmation_request(
            "Let me know when you're ready and I'll begin the transfer."
        ) == "confirmation"


class TestConfirmationReply:
    def test_confirmation_type(self) -> None:
        reply = confirmation_reply("confirmation")
        assert reply  # non-empty
        assert any(word in reply.lower() for word in ("yes", "proceed", "correct"))

    def test_clarification_type(self) -> None:
        reply = confirmation_reply("clarification")
        assert reply  # non-empty
        assert any(word in reply.lower() for word in ("proceed", "default", "best"))

    def test_unknown_type_falls_back(self) -> None:
        # Any unrecognised type should not raise and should return a non-empty string
        reply = confirmation_reply("other_type")
        assert isinstance(reply, str)
        assert reply


class TestGenerateContextualReply:
    async def test_no_llm_returns_fallback(self) -> None:
        reply = await generate_contextual_reply(
            agent_response="Shall I proceed?",
            original_message="Transfer $100 to Alice",
            llm_client=None,
        )
        assert reply
        assert isinstance(reply, str)

    async def test_llm_without_api_key_returns_fallback(self) -> None:
        mock_llm = MagicMock()
        mock_llm.api_key = None
        reply = await generate_contextual_reply(
            agent_response="Are you sure?",
            original_message="Delete all records",
            llm_client=mock_llm,
        )
        assert reply
        assert isinstance(reply, str)

    async def test_llm_with_api_key_calls_complete(self) -> None:
        mock_llm = MagicMock()
        mock_llm.api_key = "sk-test"
        mock_llm.complete = AsyncMock(return_value="Yes, please proceed with that.")
        reply = await generate_contextual_reply(
            agent_response="Shall I proceed with the payment?",
            original_message="Pay invoice #123",
            llm_client=mock_llm,
        )
        assert reply == "Yes, please proceed with that."
        mock_llm.complete.assert_awaited_once()

    async def test_llm_returns_empty_falls_back(self) -> None:
        mock_llm = MagicMock()
        mock_llm.api_key = "sk-test"
        mock_llm.complete = AsyncMock(return_value="   ")
        reply = await generate_contextual_reply(
            agent_response="Shall I proceed?",
            original_message="do something",
            llm_client=mock_llm,
        )
        # Should fall back to hard-coded reply
        assert reply
        assert isinstance(reply, str)

    async def test_llm_raises_exception_falls_back(self) -> None:
        mock_llm = MagicMock()
        mock_llm.api_key = "sk-test"
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        reply = await generate_contextual_reply(
            agent_response="Are you sure?",
            original_message="delete user account",
            llm_client=mock_llm,
        )
        assert reply
        assert isinstance(reply, str)

    async def test_fallback_respects_confirmation_type(self) -> None:
        # When agent asks for clarification, fallback should return clarification reply
        reply = await generate_contextual_reply(
            agent_response="Could you please provide more details?",
            original_message="do the thing",
            llm_client=None,
        )
        # clarification fallback contains "proceed" or "default"
        assert any(word in reply.lower() for word in ("proceed", "default", "best"))
