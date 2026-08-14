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


def _expand_flatten(yaml_text: str) -> dict:
    """Full pipeline: env expansion (mirroring load_config) then flatten."""
    from nuguard.config import _expand_env_vars

    data = yaml.safe_load(textwrap.dedent(yaml_text))
    return _flatten_yaml(_expand_env_vars(data))


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


class TestRedteamPromptCacheDirConfig:
    def test_flatten_yaml_redteam_prompt_cache_dir(self) -> None:
        flat = _flatten("""
            redteam:
              prompt_cache_dir: ./tmp/redteam-cache
        """)
        assert flat["redteam_prompt_cache_dir"] == "./tmp/redteam-cache"

    def test_default_redteam_prompt_cache_dir(self) -> None:
        cfg = NuGuardConfig()
        assert cfg.redteam_prompt_cache_dir == "."


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


# ── Shared target: block ─────────────────────────────────────────────────────


class TestSharedTargetBlock:
    """top-level target: block propagates to both redteam and behavior config."""

    def test_target_url_set_from_shared_block(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
        """)
        assert flat["target_url"] == "http://shared.test"

    def test_redteam_target_overrides_shared_url(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
            redteam:
              target: http://redteam-override.test
        """)
        assert flat["target_url"] == "http://redteam-override.test"

    def test_behavior_target_overrides_shared_url(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
            behavior:
              target: http://behavior-override.test
        """)
        # behavior_config dict carries the behavior-level override
        assert flat["behavior_config"]["target"] == "http://behavior-override.test"
        # target_url (redteam) still resolves to shared
        assert flat["target_url"] == "http://shared.test"

    def test_shared_endpoint_propagates_to_redteam(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              endpoint: /api/chat
        """)
        assert flat["target_endpoint"] == "/api/chat"

    def test_redteam_endpoint_overrides_shared_endpoint(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              endpoint: /api/chat
            redteam:
              target_endpoint: /api/redteam
        """)
        assert flat["target_endpoint"] == "/api/redteam"

    def test_redteam_endpoint_alias_overrides_shared_endpoint(self) -> None:
        """The shared ``target:`` block accepts both ``endpoint:`` and
        ``target_endpoint:`` as aliases for the same field. The redteam
        override block must accept the same alias so users who write
        ``redteam.endpoint:`` (the canonical form documented in
        nuguard.yaml.example line 47) actually see their override take
        effect. Previously the alias was silently ignored."""
        flat = _flatten("""
            target:
              url: http://shared.test
              endpoint: /api/chat
            redteam:
              endpoint: /api/redteam
        """)
        assert flat["target_endpoint"] == "/api/redteam"

    def test_behavior_endpoint_alias_overrides_shared_endpoint(self) -> None:
        """Same contract for the behavior override block: ``behavior.endpoint:``
        must override the shared ``target.endpoint:`` value, mirroring the
        alias-pair behavior the shared block already provides."""
        flat = _flatten("""
            target:
              url: http://shared.test
              endpoint: /api/chat
            behavior:
              endpoint: /api/behavior
        """)
        assert flat["behavior_config"]["target_endpoint"] == "/api/behavior"

    def test_redteam_endpoint_wins_over_target_endpoint_when_both_set(self) -> None:
        """Precedence mirrors the shared ``target:`` block: ``endpoint`` wins
        over ``target_endpoint`` when both keys are set in the same block.

        Reviewer nit on PR #257: the previous override block used the
        inverted order (``target_endpoint`` wins), which was inconsistent
        with the shared block. When a user explicitly sets both keys,
        ``endpoint`` (the canonical form documented in nuguard.yaml.example)
        must take precedence.
        """
        flat = _flatten("""
            target:
              url: http://shared.test
              endpoint: /api/chat
            redteam:
              endpoint: /api/redteam-canonical
              target_endpoint: /api/redteam-alias
        """)
        assert flat["target_endpoint"] == "/api/redteam-canonical"

    def test_behavior_endpoint_wins_over_target_endpoint_when_both_set(self) -> None:
        """Same precedence contract for the behavior override block: when
        both ``behavior.endpoint`` and ``behavior.target_endpoint`` are set,
        ``endpoint`` (canonical) wins. Mirrors the shared ``target:`` block.
        """
        flat = _flatten("""
            target:
              url: http://shared.test
              endpoint: /api/chat
            behavior:
              endpoint: /api/behavior-canonical
              target_endpoint: /api/behavior-alias
        """)
        assert flat["behavior_config"]["target_endpoint"] == "/api/behavior-canonical"

    def test_redteam_target_endpoint_used_when_endpoint_absent(self) -> None:
        """The long-form ``target_endpoint:`` still works as an alias when
        ``endpoint:`` is absent — we only flipped the precedence when both
        are present, not the alias resolution itself.
        """
        flat = _flatten("""
            target:
              url: http://shared.test
            redteam:
              target_endpoint: /api/redteam-long
        """)
        assert flat["target_endpoint"] == "/api/redteam-long"

    def test_behavior_target_endpoint_used_when_endpoint_absent(self) -> None:
        """Long-form ``behavior.target_endpoint:`` still wins the shared
        ``target.endpoint:`` value when ``behavior.endpoint:`` is absent.
        """
        flat = _flatten("""
            target:
              url: http://shared.test
              endpoint: /api/chat
            behavior:
              target_endpoint: /api/behavior-long
        """)
        assert flat["behavior_config"]["target_endpoint"] == "/api/behavior-long"

    def test_shared_chat_payload_key_propagates(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              chat_payload_key: phrases
              chat_payload_list: true
              chat_response_key: prognosis
        """)
        assert flat["redteam_chat_payload_key"] == "phrases"
        assert flat["redteam_chat_payload_list"] is True
        assert flat["redteam_chat_response_key"] == "prognosis"

    def test_shared_headers_propagate_to_redteam(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              headers:
                X-Tenant-Id: tenant-1
                X-Custom: value
        """)
        assert flat["redteam_headers"]["X-Tenant-Id"] == "tenant-1"
        assert flat["redteam_headers"]["X-Custom"] == "value"

    def test_shared_headers_injected_into_behavior_config(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              headers:
                X-Tenant-Id: tenant-1
            behavior:
              llm: true
        """)
        # The shared headers block is documented as applying to behavior as
        # well as redteam ("Extra HTTP headers added to every request
        # (behavior + redteam)" in nuguard.yaml.example), so it must be
        # injected into behavior_config.
        assert flat["behavior_config"]["headers"]["X-Tenant-Id"] == "tenant-1"

    def test_behavior_headers_override_shared_headers_in_behavior_config(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              headers:
                X-Tenant-Id: tenant-shared
            behavior:
              headers:
                X-Tenant-Id: tenant-behavior
        """)
        assert flat["behavior_config"]["headers"]["X-Tenant-Id"] == "tenant-behavior"

    def test_shared_headers_reach_resolved_behavior_config(self) -> None:
        """End-to-end: shared target.headers must survive into the resolved
        BehaviorConfig model so the behavior runner can attach them."""
        cfg = NuGuardConfig(
            behavior_config={
                "target": "http://shared.test",
                "headers": {"X-Tenant-Id": "tenant-1"},
            }
        )
        assert cfg.behavior_config.headers == {"X-Tenant-Id": "tenant-1"}

    def test_shared_bearer_auth_propagates(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              auth:
                type: bearer
                header: "Authorization: Bearer shared-tok"
        """)
        assert flat["redteam_auth_type"] == "bearer"
        assert flat["redteam_auth_header"] == "Authorization: Bearer shared-tok"

    def test_redteam_auth_overrides_shared_auth(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              auth:
                type: bearer
                header: "Authorization: Bearer shared-tok"
            redteam:
              auth:
                type: bearer
                header: "Authorization: Bearer redteam-tok"
        """)
        assert flat["redteam_auth_header"] == "Authorization: Bearer redteam-tok"

    def test_shared_login_flow_auth_propagates(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              auth:
                type: login_flow
                login_flow:
                  endpoint: /api/login
                  method: POST
                  payload:
                    username: alice
                    password: secret
                  token_response_key: access_token
                  token_header: "Authorization: Bearer"
                  refresh_on_401: true
        """)
        assert flat["redteam_auth_type"] == "login_flow"
        assert flat["redteam_auth_login_flow"]["endpoint"] == "/api/login"
        assert flat["redteam_auth_login_flow"]["token_response_key"] == "access_token"

    def test_shared_auth_injected_into_behavior_config(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              auth:
                type: bearer
                header: "Authorization: Bearer shared-tok"
            behavior:
              llm: true
        """)
        # behavior_config should have the shared auth as a default
        assert flat["behavior_config"]["auth"]["type"] == "bearer"
        assert flat["behavior_config"]["auth"]["header"] == "Authorization: Bearer shared-tok"

    def test_behavior_auth_overrides_shared_auth_in_behavior_config(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
              auth:
                type: bearer
                header: "Authorization: Bearer shared-tok"
            behavior:
              auth:
                type: api_key
                header: "X-API-Key: behavior-key"
        """)
        assert flat["behavior_config"]["auth"]["type"] == "api_key"
        assert flat["behavior_config"]["auth"]["header"] == "X-API-Key: behavior-key"

    def test_shared_url_injected_into_behavior_config_as_target(self) -> None:
        flat = _flatten("""
            target:
              url: http://shared.test
            behavior:
              llm: true
        """)
        assert flat["behavior_config"]["target"] == "http://shared.test"

    def test_no_target_block_leaves_target_url_unset(self) -> None:
        flat = _flatten("""
            redteam:
              profile: ci
        """)
        assert "target_url" not in flat

    def test_empty_target_block_is_harmless(self) -> None:
        flat = _flatten("""
            target: {}
        """)
        assert "target_url" not in flat


class TestUnsetEnvVarNoneFiltering:
    """Unset ${VAR} references must never leak the literal string "None".

    ``_expand_env_vars`` turns an unset placeholder into ``None``.  The
    flattening step must then drop those ``None`` values (as it already does
    for ``redteam.credentials`` and the LLM blocks) instead of stringifying
    them, so a header like ``X-Tenant-Id: ${MY_TENANT}`` cannot reach the
    HTTP client as ``X-Tenant-Id: None``.
    """

    def test_shared_headers_drop_unset_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_TENANT", raising=False)
        flat = _expand_flatten("""
            target:
              url: http://shared.test
              headers:
                X-Tenant-Id: ${MY_TENANT}
                X-API-Key: static-key
        """)
        assert flat["redteam_headers"] == {"X-API-Key": "static-key"}
        assert "None" not in str(flat["redteam_headers"])

    def test_redteam_headers_drop_unset_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_TENANT", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              headers:
                X-Tenant-Id: ${MY_TENANT}
                X-API-Key: static-key
        """)
        assert flat["redteam_headers"] == {"X-API-Key": "static-key"}

    def test_shared_headers_behavior_injection_drops_unset_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MY_TENANT", raising=False)
        flat = _expand_flatten("""
            target:
              url: http://shared.test
              headers:
                X-Tenant-Id: ${MY_TENANT}
                X-API-Key: static-key
            behavior:
              llm: true
        """)
        assert flat["behavior_config"]["headers"] == {"X-API-Key": "static-key"}

    def test_redteam_app_env_drops_unset_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_SECRET", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              app_env:
                MY_SECRET: ${MY_SECRET}
                KEEP_ME: value
        """)
        assert flat["redteam_app_env"] == {"KEEP_ME": "value"}

    def test_redteam_customer_profile_drops_unset_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CUSTOMER_PROFILE", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              app_env:
                KEEP_ME: value
              customer_profile: ${CUSTOMER_PROFILE}
        """)
        assert flat["redteam_app_env"] == {"KEEP_ME": "value"}

    def test_env_default_fallback_still_keeps_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_TENANT", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              headers:
                X-Tenant-Id: ${MY_TENANT:-fallback-tenant}
        """)
        assert flat["redteam_headers"] == {"X-Tenant-Id": "fallback-tenant"}


class TestUnsetEnvVarScalarFields:
    """Unset ${VAR} in scalar redteam fields must not crash config loading.

    ``_flatten_yaml`` used to stringify the ``None`` produced by an unset
    placeholder into the literal ``"None"`` for scalar fields.  For
    Literal-typed fields (``engine``, ``guided_mutation_mode``) that crashed
    ``load_config`` with a pydantic ValidationError — and unlike the behavior
    command, ``nuguard redteam`` does not wrap ``load_config`` in a
    try/except, so the raw traceback escaped to the user.  Path-valued
    fields silently carried a literal ``'None'`` path (``Path('.../None')``)
    into runtime, where ``PromptCache.save`` would create a directory named
    ``None``.
    """

    def test_engine_unset_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RT_ENGINE", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              engine: ${RT_ENGINE}
        """)
        assert "redteam_engine" not in flat
        from nuguard.config import NuGuardConfig

        cfg = NuGuardConfig(**flat)
        assert cfg.redteam_engine == "v1"

    def test_guided_mutation_mode_unset_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GMM", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              guided_mutation_mode: ${GMM}
        """)
        assert "redteam_guided_mutation_mode" not in flat
        from nuguard.config import NuGuardConfig

        cfg = NuGuardConfig(**flat)
        assert cfg.redteam_guided_mutation_mode == "hard"

    def test_scenarios_drop_unset_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("M", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              scenarios:
                - ${M}
                - policy-violation
        """)
        assert flat["redteam_scenarios"] == ["policy-violation"]

    def test_prompt_cache_dir_unset_not_none_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PCD", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              prompt_cache_dir: ${PCD}
        """)
        assert "redteam_prompt_cache_dir" not in flat

    def test_catalog_path_unset_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CP", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              catalog_path: ${CP}
        """)
        assert "redteam_catalog_path" not in flat

    def test_emit_pytest_dir_unset_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("EPD", raising=False)
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              emit_pytest_dir: ${EPD}
        """)
        assert "emit_pytest_dir" not in flat

    def test_engine_set_via_env_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RT_ENGINE", "v2")
        flat = _expand_flatten("""
            redteam:
              target: http://app.test
              engine: ${RT_ENGINE}
        """)
        assert flat["redteam_engine"] == "v2"
