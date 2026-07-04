"""Unit tests for nuguard.redteam.executor.placeholder_data."""
from __future__ import annotations

from nuguard.redteam.executor.placeholder_data import (
    is_placeholder_email,
    is_placeholder_id,
    is_placeholder_name,
    is_placeholder_phone,
)


def test_placeholder_names_detected():
    for name in ("Jane Doe", "john doe", "John Smith", "Test User"):
        assert is_placeholder_name(name) is True


def test_real_name_not_placeholder():
    assert is_placeholder_name("Alice Johnson") is False


def test_placeholder_email_domains_detected():
    for email in ("user@example.com", "test@example.org", "name@domain.com"):
        assert is_placeholder_email(email) is True


def test_real_email_domain_not_placeholder():
    assert is_placeholder_email("bob.smith@corp.com") is False


def test_placeholder_phone_reserved_range_detected():
    for phone in ("555-0123", "+1-555-0199", "(555) 0100"):
        assert is_placeholder_phone(phone) is True


def test_real_phone_outside_reserved_range_not_placeholder():
    assert is_placeholder_phone("415-867-5309") is False


def test_placeholder_id_with_x_filler_detected():
    for identifier in ("CUST-XXXXX", "ID-XXXX", "acct-xxxxxx"):
        assert is_placeholder_id(identifier) is True


def test_zero_padded_id_not_treated_as_placeholder():
    """Zero-padded sequential IDs (ACCT-00001, ACCT-00002) are a common REAL ID
    convention — must not be excluded as placeholders, or genuine cross-account
    findings would be silently suppressed."""
    for identifier in ("ACCT-00001", "ACCT-00002", "USER-000"):
        assert is_placeholder_id(identifier) is False
