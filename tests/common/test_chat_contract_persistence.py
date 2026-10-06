"""Verify every preflight caller refuses to persist an unvalidated contract."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from nuguard.behavior.tests import test_runner as behavior_fixtures
from nuguard.common.discovery import DiscoveredProfile, DiscoveryOutcome
from nuguard.common.endpoint_preflight import validate_and_rotate_chat_endpoint
from nuguard.common.target_verify_public_api import TargetVerifyRequest, verify_target
from nuguard.redteam.tests import test_orchestrator_endpoint_cache as redteam_fixtures
from nuguard.sbom.models import AiSbomDocument
from tests.cli import test_target_verify_endpoint_cache as cli_fixtures
from tests.common import test_target_verify_public_api as public_fixtures

SECRET = "issue627-persistence-fixture-secret"


class _EvidenceClient:
    chat_path = "/chat"
    last_raw_response: dict | None = None

    def __init__(self, failure: str | None) -> None:
        self.failure = failure

    async def send(self, message, session):
        if self.failure == "transport":
            raise httpx.ReadTimeout(SECRET)
        if self.failure == "no_reply":
            self.last_raw_response = {"session_id": "id", "status": "ok"}
            return json.dumps(self.last_raw_response), []
        self.last_raw_response = {"response": "Actual assistant reply"}
        return "Actual assistant reply", []


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["cli", "public_api", "behavior", "redteam"])
@pytest.mark.parametrize("failure", ["no_reply", "transport"])
async def test_unvalidated_contract_is_not_persisted_and_next_run_recovers(
    consumer: str,
    failure: str,
    monkeypatch,
    tmp_path,
    caplog,
) -> None:
    caplog.set_level(logging.DEBUG)
    failed = await validate_and_rotate_chat_endpoint(
        _EvidenceClient(failure),
        None,
        has_explicit_endpoint=True,
    )
    succeeded = await validate_and_rotate_chat_endpoint(
        _EvidenceClient(None),
        None,
        has_explicit_endpoint=True,
    )
    assert not failed.cacheable and succeeded.cacheable
    outcomes = AsyncMock(side_effect=[failed, succeeded])
    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", outcomes
    )
    sbom = AiSbomDocument(target="./app", nodes=[], edges=[])
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json(), encoding="utf-8")
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")

    async def run_once():
        if consumer == "cli":
            cli_fixtures._mock_client_builder(monkeypatch, cli_fixtures._FakeDiscoveryClient())
            cli_fixtures._mock_discovery(monkeypatch)
            return await cli_fixtures._run_pre_scan_discovery(
                sbom,
                cli_fixtures._session_cfg(),
                sbom_path=sbom_path,
                max_turns=1,
                has_explicit_endpoint=True,
                preflight_candidates=1,
            )
        if consumer == "public_api":
            monkeypatch.setattr(
                "nuguard.common.target_verify_public_api.validate_and_rotate_chat_endpoint",
                outcomes,
            )
            monkeypatch.setattr(
                "nuguard.common.target_verify_public_api.resolve_target_session",
                public_fixtures._fake_resolve_target_session_factory(
                    public_fixtures._session_config()
                ),
            )
            monkeypatch.setattr(
                "nuguard.common.target_verify_public_api.build_target_app_client_from_session",
                lambda *a, **kw: public_fixtures._PreflightFakeClient(),
            )
            monkeypatch.setattr(
                "nuguard.common.target_verify_public_api.run_discovery",
                AsyncMock(return_value=DiscoveryOutcome(profile=DiscoveredProfile())),
            )
            result = await verify_target(
                TargetVerifyRequest(target_url="http://target"), sbom=sbom, sbom_path=sbom_path
            )
            assert type(result).model_validate_json(result.model_dump_json()) == result
            assert SECRET not in result.model_dump_json()
            return result
        if consumer == "behavior":
            runner = behavior_fixtures._make_preflight_runner(sbom, explicit=True)
            runner._resolved_target_url = behavior_fixtures._DISCOVERY_TARGET_URL
            runner._sbom_path = sbom_path
            return await runner._ensure_endpoint_preflight(behavior_fixtures._FakePreflightClient())
        orchestrator = redteam_fixtures._make_orchestrator(
            sbom, chat_path="/chat", sbom_path=sbom_path
        )
        return await orchestrator._ensure_endpoint_preflight(
            redteam_fixtures._FakeClient(), auth_headers=None
        )

    await run_once()
    assert sbom.resolved_chat_endpoint is None
    assert sbom.resolved_chat_endpoint_fingerprint is None
    for artifact in tmp_path.glob("*.json"):
        data = json.loads(artifact.read_text(encoding="utf-8"))
        assert data.get("resolved_chat_endpoint") is None
        assert SECRET not in artifact.read_text(encoding="utf-8")
    await run_once()
    assert sbom.resolved_chat_endpoint is not None
    assert enriched_path.exists()
    restored = AiSbomDocument.model_validate_json(enriched_path.read_text(encoding="utf-8"))
    assert restored.resolved_chat_endpoint == sbom.resolved_chat_endpoint
    assert SECRET not in caplog.text + sbom.model_dump_json() + enriched_path.read_text(
        encoding="utf-8"
    )
    assert outcomes.await_count == 2
