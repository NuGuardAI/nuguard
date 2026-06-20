"""Regression test: bootstrap failures (None auth session) must not crash."""
from nuguard.common.session_resolver import _merge_login_response_extras


def test_merge_login_response_extras_handles_no_session():
    """When bootstrap couldn't establish a session (e.g. user authed the chat
    endpoint directly instead of an SBOM-declared auth endpoint), merging
    must fall back to the static config instead of raising."""
    extras, notes = _merge_login_response_extras(None, {"foo": "bar"})
    assert extras == {"foo": "bar"}
    assert notes == []
