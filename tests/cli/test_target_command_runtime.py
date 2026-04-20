"""Runtime-focused tests for target verify auth helper integration."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
import typer

from nuguard.common.auth import AuthConfig
from nuguard.common.auth_runtime import ResolvedAuthRuntime
from nuguard.models.health_report import CredentialCheckResult, TargetHealthReport


@dataclass
class _ConfigStub:
    target_url: str | None = None
    target_endpoint: str | None = None
    canary_path: str | None = None
    redteam_headers: dict[str, str] | None = None

    def resolved_auth_config(self) -> AuthConfig:
        return AuthConfig(type="none")


def _ok_report(target_url: str, endpoint: str) -> TargetHealthReport:
    return TargetHealthReport(
        target_url=target_url,
        endpoint=endpoint,
        run_id="test-run",
        checks=[
            CredentialCheckResult(
                identity="default",
                auth_type="bearer",
                endpoint=f"{target_url}{endpoint}",
                status="ok",
                http_status_code=200,
            )
        ],
    )


@pytest.mark.asyncio
async def test_verify_redteam_uses_headers_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from nuguard.cli.commands import target as target_cmd

    cfg = _ConfigStub(
        target_url="http://redteam.test",
        target_endpoint="/chat",
        redteam_headers={"Authorization": "Bearer override-token"},
    )
    monkeypatch.setattr(target_cmd, "load_config", lambda _p: cfg)

    captured: dict[str, object] = {}

    def _fake_resolve_auth_runtime(
        auth_config: AuthConfig | None = None,
        headers_override: dict[str, str] | None = None,
    ) -> ResolvedAuthRuntime:
        captured["headers_override"] = headers_override
        return ResolvedAuthRuntime(
            auth_config=AuthConfig(type="bearer", header="Authorization: Bearer final-token"),
            initial_headers={"Authorization": "Bearer final-token"},
        )

    async def _fake_bootstrap_auth_runtime(**kwargs: object) -> tuple[object, TargetHealthReport]:
        captured["bootstrap_auth_type"] = getattr(kwargs.get("auth_config"), "type", None)
        return object(), _ok_report("http://redteam.test", "/chat")

    monkeypatch.setattr(target_cmd, "resolve_auth_runtime", _fake_resolve_auth_runtime)
    monkeypatch.setattr(target_cmd, "bootstrap_auth_runtime", _fake_bootstrap_auth_runtime)

    with pytest.raises(typer.Exit) as exc:
        await target_cmd._verify_async(
            config_path=None,
            target_override=None,
            endpoint_override=None,
            auth_header_override=None,
            canary_path=None,
        )

    assert exc.value.exit_code == 0
    assert captured["headers_override"] == {"Authorization": "Bearer override-token"}
    assert captured["bootstrap_auth_type"] == "bearer"
