"""Tests for nuguard.config — _flatten_yaml() and NuGuardConfig helpers."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from nuguard.config import (
    NuGuardConfig,
    _flatten_yaml,
    load_config,
)

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


class TestGuidedMutationModeConfig:
    def test_flatten_yaml_guided_mutation_mode(self) -> None:
        flat = _flatten("""
            redteam:
              guided_mutation_mode: soft
        """)
        assert flat["redteam_guided_mutation_mode"] == "soft"

    def test_env_guided_mutation_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NUGUARD_REDTEAM_GUIDED_MUTATION_MODE", "soft")
        cfg = NuGuardConfig()
        assert cfg.redteam_guided_mutation_mode == "soft"
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

        def test_login_flow_block(self) -> None:
                flat = _flatten("""
                        redteam:
                            target: http://app.test
                            auth:
                                type: login_flow
                                login_flow:
                                    endpoint: /login
                                    method: POST
                                    payload:
                                        username: alice
                                        password: secret
                                    token_response_key: data.token
                                    token_header: "Authorization: Bearer"
                                    refresh_on_401: true
                """)
                assert flat["redteam_auth_type"] == "login_flow"
                assert flat["redteam_auth_login_flow"]["endpoint"] == "/login"
                assert flat["redteam_auth_login_flow"]["token_response_key"] == "data.token"

    def test_legacy_auth_header_string_still_works(self) -> None:
        flat = _flatten("""
            redteam:
              target: http://app.test
              auth_header: "Authorization: Bearer legacytok"
        """)
        assert flat["redteam_auth_header"] == "Authorization: Bearer legacytok"
        # structured auth_type not set from legacy path
        assert "redteam_auth_type" not in flat

        def test_explicit_headers_override_block(self) -> None:
                flat = _flatten("""
                        redteam:
                            target: http://app.test
                            headers:
                                Authorization: "Bearer token-from-json"
                                X-Tenant-Id: tenant-1
                """)
                assert flat["redteam_headers"]["Authorization"] == "Bearer token-from-json"
                assert flat["redteam_headers"]["X-Tenant-Id"] == "tenant-1"

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

    def test_login_flow_structured(self) -> None:
        from nuguard.common.auth import LoginFlowConfig

        cfg = self._cfg(
            redteam_auth_type="login_flow",
            redteam_auth_login_flow=LoginFlowConfig(
                endpoint="/login",
                method="POST",
                payload={"username": "alice", "password": "secret"},
                token_response_key="access_token",
                token_header="Authorization: Bearer",
                refresh_on_401=True,
            ),
        )
        auth = cfg.resolved_auth_config()
        assert auth.type == "login_flow"
        assert auth.login_flow is not None
        assert auth.login_flow.endpoint == "/login"

    def test_fallback_to_legacy_header_string(self) -> None:
        cfg = self._cfg(redteam_auth_header="Authorization: Bearer legacytok")
        auth = cfg.resolved_auth_config()
        assert auth.type == "bearer"

    def test_none_when_nothing_configured(self) -> None:
        cfg = self._cfg()
        auth = cfg.resolved_auth_config()
        assert auth.type == "none"
        assert auth.to_headers() == {}

    def test_header_override_field_is_preserved(self) -> None:
        cfg = self._cfg(
            redteam_headers={
                "Authorization": "Bearer override-token",
                "X-Tenant-Id": "tenant-2",
            }
        )
        assert cfg.redteam_headers["Authorization"] == "Bearer override-token"
        assert cfg.redteam_headers["X-Tenant-Id"] == "tenant-2"


class TestLoadConfigPathResolution:
    def test_rebases_relative_paths_against_config_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_dir = tmp_path / "fixtures" / "stateset"
        cfg_dir.mkdir(parents=True)

        config_file = cfg_dir / "nuguard.yaml"
        config_file.write_text(
            textwrap.dedent(
                """
                sbom: reports/app.sbom.json
                source: ../source-dir
                policy:
                  path: cognitive_policy.md
                redteam:
                  canary: canary.json
                output:
                  sarif_file: reports/findings.sarif
                """
            ),
            encoding="utf-8",
        )

        # Run from a different cwd to ensure resolution is based on config dir.
        monkeypatch.chdir(tmp_path)
        cfg = load_config(config_file)

        assert cfg.sbom_path == str((cfg_dir / "reports" / "app.sbom.json").resolve())
        assert cfg.source_path == str((cfg_dir / ".." / "source-dir").resolve())
        assert cfg.policy_path == str((cfg_dir / "cognitive_policy.md").resolve())
        assert cfg.canary_path == str((cfg_dir / "canary.json").resolve())
        assert cfg.sarif_output_path == str(
            (cfg_dir / "reports" / "findings.sarif").resolve()
        )

    def test_prefers_repo_root_when_repo_relative_path_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = tmp_path / "repo"
        cfg_dir = repo_root / "tests" / "apps" / "stateset"
        cfg_dir.mkdir(parents=True)

        # Mark this as repository root for config path resolution.
        (repo_root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

        sbom_rel = Path("tests/apps/stateset/reports/app.sbom.json")
        sbom_abs = (repo_root / sbom_rel)
        sbom_abs.parent.mkdir(parents=True)
        sbom_abs.write_text("{}", encoding="utf-8")

        config_file = cfg_dir / "nuguard.yaml"
        config_file.write_text(
            textwrap.dedent(
                """
                sbom: tests/apps/stateset/reports/app.sbom.json
                """
            ),
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        cfg = load_config(config_file)

        assert cfg.sbom_path == str(sbom_abs.resolve())
