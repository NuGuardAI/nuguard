"""Credential auto-supply for behavior runs.

All logic has moved to :mod:`nuguard.common.credentials`.  This module
re-exports everything from there for backward compatibility.
"""
from nuguard.common.credentials import (  # noqa: F401
    confirmation_reply,
    credential_reply,
    detect_confirmation_request,
    detect_credential_request,
    detect_login_success,
    generate_contextual_reply,
    get_credential,
)
