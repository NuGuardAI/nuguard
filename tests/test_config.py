"""Tests for nuguard.config — _flatten_yaml() and NuGuardConfig helpers."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from nuguard.config import NuGuardConfig, ValidateConfig, _flatten_yaml


# ── _flatten_yaml helpers ────────────────────────────────────────────────────


def _flatten(yaml_text: str) -> dict:
    data = yaml.safe_load(textwrap.dedent(yaml_text))
    return _flatten_yaml(data)


class TestFlattenYamlValidateSection:
    def test_validate_target_goes_to_validate_config(self) -> None:
        flat = _flatten("""
            validate:
              target: http://validate.test
        """)
        # validate_config dict contains the target
        assert flat["validate_config"]["target"] == "http://validate.test"
        # should NOT overwrite redteam target_url
        assert "target_url" not in flat

    def test_validate_and_redteam_independent(self) -> None:
        flat = _flatten("""
            validate:
              target: http://validate.test
            redteam:
              target: http://redteam.test
        """)
        assert flat["validate_config"]["target"] == "http://validate.test"
        assert flat["target_url"] == "http://redteam.test"

    def test_validate_auth_preserved(self) -> None:
        flat = _flatten("""
            validate:
              target: http://validate.test
              auth:
                type: bearer
                header: "Authorization: Bearer vtok"
        """)
        assert flat["validate_config"]["auth"]["type"] == "bearer"


class TestFlattenYamlRedteamAuth:
    def test_bearer_block(self) -> None:
        flat = _flatten("""
            redteam:
              target: http://app.test
              auth:
                type: bearer
                header: "Authorization: Bearer tok"
        """)
        assert flat["redteam_auth_type"] == "bearer"
        assert flat["redteam_auth_header"] == "Authorization: Bearer tok"

    def test_basic_block(self) -> None:
        flat = _flatten("""
            redteam:
              target: http://app.test
              auth:
                type: basic
                username: alice
                password: secret
        """)
        assert flat["redteam_auth_type"] == "basic"
        assert flat["redteam_auth_username"] == "alice"
        assert flat["redteam_auth_password"] == "secret"

    def test_legacy_auth_header_string_still_works(self) -> None:
        flat = _flatten("""
            redteam:
              target: http://app.test
              auth_header: "Authorization: Bearer legacytok"
        """)
        assert flat["redteam_auth_header"] == "Authorization: Bearer legacytok"
        # structured auth_type not set from legacy path
        assert "redteam_auth_type" not in flat

    def test_defence_regressions_parsed(self) -> None:
        flat = _flatten("""
            redteam:
              defence_regressions:
                - name: block_injection
                  message: "override"
                  expect: refused
                  severity: high
        """)
        assert len(flat["redteam_defence_regressions"]) == 1
        assert flat["redteam_defence_regressions"][0]["name"] == "block_injection"


class TestResolvedAuthConfig:
    def _cfg(self, **overrides: object) -> NuGuardConfig:
        return NuGuardConfig(**overrides)  # type: ignore[arg-type]

    def test_bearer_structured(self) -> None:
        cfg = self._cfg(
            redteam_auth_type="bearer",
            redteam_auth_header="Authorization: Bearer tok",
        )
        auth = cfg.resolved_auth_config()
        assert auth.type == "bearer"
        assert auth.to_headers() == {"Authorization": "Bearer tok"}

    def test_basic_structured(self) -> None:
        cfg = self._cfg(
            redteam_auth_type="basic",
            redteam_auth_username="alice",
            redteam_auth_password="pass",
        )
        auth = cfg.resolved_auth_config()
        assert auth.type == "basic"
        assert "Authorization" in auth.to_headers()

    def test_fallback_to_legacy_header_string(self) -> None:
        cfg = self._cfg(redteam_auth_header="Authorization: Bearer legacytok")
        auth = cfg.resolved_auth_config()
        assert auth.type == "bearer"

    def test_none_when_nothing_configured(self) -> None:
        cfg = self._cfg()
        auth = cfg.resolved_auth_config()
        assert auth.type == "none"
        assert auth.to_headers() == {}


class TestResolvedValidateAuthConfig:
    def _cfg_with_validate(self, validate_dict: dict) -> NuGuardConfig:
        return NuGuardConfig(validate_config=ValidateConfig(**validate_dict))  # type: ignore[arg-type]

    def test_bearer(self) -> None:
        from nuguard.config import ValidateAuthConfig

        cfg = self._cfg_with_validate({
            "auth": ValidateAuthConfig(type="bearer", header="Authorization: Bearer vtok")
        })
        auth = cfg.resolved_validate_auth_config()
        assert auth.type == "bearer"
        assert auth.to_headers() == {"Authorization": "Bearer vtok"}

    def test_none_when_not_configured(self) -> None:
        cfg = NuGuardConfig()
        auth = cfg.resolved_validate_auth_config()
        assert auth.type == "none"
