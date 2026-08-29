"""Tests for target authentication recovery guidance."""

import pytest

from nuguard.cli.commands.target import _browser_session_refresh_hint

_REFRESH_HINT = (
    "The saved browser session may have expired. "
    "Refresh it with: nuguard target discover-browser --write --yes"
)


@pytest.mark.parametrize("status_code", [401, 403])
def test_browser_session_refresh_hint_for_cookie_auth(
    status_code: int,
) -> None:
    hint = _browser_session_refresh_hint(
        status_code=status_code,
        identity="default",
        auth_type="cookie_file",
    )

    assert hint == _REFRESH_HINT


@pytest.mark.parametrize(
    ("status_code", "identity", "auth_type"),
    [
        pytest.param(
            401,
            "default",
            "bearer",
            id="non-cookie-auth",
        ),
        pytest.param(
            403,
            "canary:tenant-a",
            "cookie_file",
            id="non-default-identity",
        ),
        pytest.param(
            500,
            "default",
            "cookie_file",
            id="unrelated-http-status",
        ),
        pytest.param(
            None,
            "default",
            "cookie_file",
            id="missing-http-status",
        ),
    ],
)
def test_browser_session_refresh_hint_not_returned_otherwise(
    status_code: int | None,
    identity: str,
    auth_type: str,
) -> None:
    hint = _browser_session_refresh_hint(
        status_code=status_code,
        identity=identity,
        auth_type=auth_type,
    )

    assert hint is None
