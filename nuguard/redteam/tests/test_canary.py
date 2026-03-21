"""Unit tests for nuguard.redteam.target.canary.CanaryConfig and CanaryScanner."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from nuguard.redteam.target.canary import CanaryConfig, CanaryRecord, CanaryScanner, CanaryTenant


def _make_config(
    global_values: list[str] | None = None,
    tenant_values: list[str] | None = None,
) -> CanaryConfig:
    records = []
    if tenant_values:
        records.append(
            CanaryRecord(
                resource="users",
                id="u1",
                fields={"email": "canary@example.com"},
                watch_values=tenant_values,
            )
        )
    tenants = []
    if records:
        tenants.append(CanaryTenant(tenant_id="t1", records=records))
    return CanaryConfig(
        global_watch_values=global_values or [],
        tenants=tenants,
    )


def test_all_watch_values_combines_global_and_tenant() -> None:
    config = _make_config(
        global_values=["CANARY-SECRET-1234"],
        tenant_values=["canary@example.com"],
    )
    values = config.all_watch_values()
    assert "CANARY-SECRET-1234" in values
    assert "canary@example.com" in values


def test_all_watch_values_deduplicated() -> None:
    config = _make_config(
        global_values=["SHARED-VALUE"],
        tenant_values=["SHARED-VALUE"],
    )
    values = config.all_watch_values()
    assert values.count("SHARED-VALUE") == 1


def test_all_watch_values_global_only() -> None:
    config = _make_config(global_values=["GLOBAL-ONLY"])
    assert "GLOBAL-ONLY" in config.all_watch_values()


def test_canary_config_load_from_json(tmp_path: Path) -> None:
    data = {
        "global_watch_values": ["TEST-CANARY"],
        "tenants": [],
    }
    config_file = tmp_path / "canary.json"
    config_file.write_text(json.dumps(data))
    config = CanaryConfig.load(config_file)
    assert "TEST-CANARY" in config.global_watch_values


def test_scan_returns_empty_on_no_match() -> None:
    config = _make_config(global_values=["CANARY-SECRET-1234"])
    scanner = CanaryScanner(config)
    result = scanner.scan("this text has nothing interesting")
    assert result == []


def test_scan_returns_matching_verbatim_value() -> None:
    config = _make_config(global_values=["CANARY-SECRET-1234"])
    scanner = CanaryScanner(config)
    result = scanner.scan("response contains CANARY-SECRET-1234 in plain text")
    assert "CANARY-SECRET-1234" in result


def test_scan_is_case_sensitive() -> None:
    config = _make_config(global_values=["CANARY-SECRET-1234"])
    scanner = CanaryScanner(config)
    # Lowercase should NOT match
    assert scanner.scan("canary-secret-1234") == []


def test_scan_decoded_finds_base64_encoded_value() -> None:
    secret = "CANARY-SECRET-1234"
    encoded = base64.b64encode(secret.encode()).decode()
    config = _make_config(global_values=[secret])
    scanner = CanaryScanner(config)
    result = scanner.scan_decoded(encoded)
    assert secret in result


def test_scan_decoded_finds_hex_encoded_value() -> None:
    secret = "CANARY-SECRET"
    hex_encoded = secret.encode().hex()
    config = _make_config(global_values=[secret])
    scanner = CanaryScanner(config)
    result = scanner.scan_decoded(hex_encoded)
    assert secret in result


def test_has_match_returns_true_on_verbatim_match() -> None:
    config = _make_config(global_values=["CANARY-SECRET-1234"])
    scanner = CanaryScanner(config)
    assert scanner.has_match("The value CANARY-SECRET-1234 was exposed") is True


def test_has_match_returns_false_on_no_match() -> None:
    config = _make_config(global_values=["CANARY-SECRET-1234"])
    scanner = CanaryScanner(config)
    assert scanner.has_match("nothing here") is False


def test_scan_returns_multiple_matches() -> None:
    config = _make_config(global_values=["SECRET-A", "SECRET-B"])
    scanner = CanaryScanner(config)
    result = scanner.scan("found SECRET-A and also SECRET-B in response")
    assert "SECRET-A" in result
    assert "SECRET-B" in result
    assert len(result) == 2
