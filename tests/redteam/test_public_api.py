"""Tests for nuguard.redteam.public_api — the Pydantic-only entry point for
callers outside the CLI (v1 RedteamOrchestrator only).

Focuses on: (1) JSON round-tripping of the new request/result models,
(2) that run_redteam() constructs RedteamOrchestrator with exactly the
request's fields plus the separately-passed collaborators and reads back the
orchestrator's instance attributes into RedteamRunResult, proving no
functionality is lost relative to the CLI's `_run_orchestrator`, and (3) the
post-run scenario_filter re-check (run_redteam() is the single
implementation — the CLI calls it directly, it isn't duplicated) matches
findings on goal_type, scenario_type, and title, the same as the
orchestrator's own pre-run filter.
"""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr, ValidationError

from nuguard.common.auth import AuthConfig, LoginFlowConfig
from nuguard.config import AppAuthConfig, RedteamFindingTriggers
from nuguard.models.finding import Finding, Severity
from nuguard.models.health_report import TargetHealthReport
from nuguard.models.policy import CognitivePolicy
from nuguard.models.token_usage import TokenUsage
from nuguard.policy import CognitivePolicyParseResult
from nuguard.redteam.coverage.tracker import CoverageTracker
from nuguard.redteam.public_api import (
    RedteamAuthConfig,
    RedteamLoginFlowConfig,
    RedteamRunRequest,
    RedteamRunResult,
    run_redteam,
    run_redteam_stream,
)
from nuguard.redteam.target.canary import CanaryConfig


def _finding(goal_type: str = "prompt_driven_threat", scenario_type: str | None = None) -> Finding:
    return Finding(
        finding_id="f1",
        title="t",
        severity=Severity.HIGH,
        description="d",
        goal_type=goal_type,
        scenario_type=scenario_type,
    )


@dataclasses.dataclass
class _FakeScenarioRecord:
    title: str
    goal_type: str
    scenario_type: str = "x"
    description: str = ""
    impact_score: float = 0.0
    affected: str = ""
    chain_status: str = "completed"
    had_finding: bool = False


def _make_mock_orchestrator(findings: list[Finding]) -> MagicMock:
    instance = MagicMock()
    instance.run = AsyncMock(return_value=findings)
    instance.scenario_records = [_FakeScenarioRecord(title="s1", goal_type="prompt_driven_threat")]
    instance.scan_outcome = "high_findings"
    instance.config_notes = ["note1"]
    instance.llm_executive_summary = "summary"
    instance.scenarios_run = 3
    instance.input_tokens_used = 10
    instance.output_tokens_used = 20
    instance.token_usage = TokenUsage(input_tokens=10, output_tokens=20)
    instance.health_report = TargetHealthReport(target_url="http://x", endpoint="/api/chat", run_id="r1")
    instance.resolved_chat_path = "/api/chat"
    instance.resolved_chat_path_source = "sbom"
    instance.catalog_coverage = None
    tracker = CoverageTracker()
    tracker.record_generated("n1", "AGENT", "Agent1")
    instance._coverage_tracker = tracker
    return instance


# ---------------------------------------------------------------------------
# JSON round-trips
# ---------------------------------------------------------------------------


def test_redteam_run_request_json_roundtrip():
    req = RedteamRunRequest(
        target_url="http://x",
        canary_config=CanaryConfig(),
        auth_config=AuthConfig(type="none"),
        finding_triggers=RedteamFindingTriggers(),
        scenario_filter=["prompt-injection"],
    )
    restored = RedteamRunRequest.model_validate_json(req.model_dump_json())
    assert restored.target_url == "http://x"
    assert restored.scenario_filter == ["prompt-injection"]
    assert restored.auth_config is not None
    assert restored.auth_config.type == "none"
    assert restored.canary_config is not None


@pytest.mark.parametrize(
    "auth",
    [
        AppAuthConfig(type="bearer", header="Authorization: Bearer token"),
        AppAuthConfig(type="api_key", header="X-API-Key: token"),
        AppAuthConfig(type="basic", username="user", password="password"),
        AppAuthConfig(
            type="login_flow",
            login_flow=LoginFlowConfig(payload={"username": "user", "password": "password"}),
        ),
        AppAuthConfig(type="cookie_file", cookie_file="cookies.txt"),
        AppAuthConfig(type="none"),
    ],
)
def test_redteam_request_accepts_app_auth_config(auth: AppAuthConfig) -> None:
    """App-level auth models retain every runtime value behind the public boundary."""
    request = RedteamRunRequest(target_url="http://localhost:9999", auth_config=auth)

    assert isinstance(request.auth_config, RedteamAuthConfig)
    assert request.auth_config.to_internal().model_dump() == auth.model_dump()


def test_redteam_request_accepts_legacy_auth_config_with_nested_login_payload() -> None:
    """The existing internal auth model remains accepted without losing payload values."""
    auth = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(
            endpoint="/session",
            method="POST",
            base_url="https://identity.example.test",
            payload={
                "identity": {
                    "username": "legacy-user",
                    "factors": ["legacy-password", {"otp": "123456"}],
                },
                "remember": True,
                "attempt": 2,
                "optional": None,
            },
            token_response_key="data.access_token",
            token_header="X-Session-Token:",
            refresh_on_401=False,
        ),
    )

    request = RedteamRunRequest(target_url="http://localhost:9999", auth_config=auth)

    assert isinstance(request.auth_config, RedteamAuthConfig)
    assert request.auth_config.to_internal().model_dump() == auth.model_dump()


@pytest.mark.parametrize(
    ("auth_data", "message"),
    [
        ({"type": "bearer"}, "requires auth.header"),
        ({"type": "api_key"}, "requires auth.header"),
        ({"type": "basic", "username": "user"}, "requires auth.username and auth.password"),
        ({"type": "login_flow"}, "requires auth.login_flow block"),
        ({"type": "cookie_file"}, "requires auth.cookie_file"),
    ],
)
def test_redteam_auth_config_rejects_missing_required_fields(
    auth_data: dict[str, object],
    message: str,
) -> None:
    """The secret-safe auth model preserves the runtime model's validation contract."""
    with pytest.raises(ValidationError, match=message):
        RedteamAuthConfig.model_validate(auth_data)


@pytest.mark.parametrize(
    ("auth", "secrets"),
    [
        (RedteamAuthConfig(type="bearer", header="Authorization: Bearer bearer-secret"), ["bearer-secret"]),
        (RedteamAuthConfig(type="api_key", header="X-API-Key: api-secret"), ["api-secret"]),
        (
            RedteamAuthConfig(type="basic", username="basic-user", password="basic-password"),
            ["basic-user", "basic-password"],
        ),
        (
            RedteamAuthConfig(
                type="login_flow",
                login_flow=RedteamLoginFlowConfig(
                    payload={
                        "username": "login-user",
                        "password": "login-password",
                        "nested": {"token": "nested-secret"},
                    }
                ),
            ),
            ["login-user", "login-password", "nested-secret"],
        ),
    ],
)
def test_redteam_auth_secrets_are_redacted_from_serialization(
    auth: RedteamAuthConfig,
    secrets: list[str],
) -> None:
    request = RedteamRunRequest(target_url="http://localhost:9999", auth_config=auth)

    serialized = request.model_dump_json()

    for secret in secrets:
        assert secret not in serialized


def test_redteam_request_header_and_confirmation_credentials_are_redacted() -> None:
    """Header overrides and confirmation credentials never serialize as plaintext."""
    request = RedteamRunRequest(
        target_url="http://localhost:9999",
        extra_headers={"Authorization": "Bearer header-secret"},
        credentials={"account": "credential-secret"},
    )

    serialized = request.model_dump_json()

    assert "header-secret" not in serialized
    assert "credential-secret" not in serialized


def test_nested_login_payload_is_secret_safe_and_preserves_non_string_values() -> None:
    """Nested objects and arrays redact strings without changing JSON scalar values."""
    payload = {
        "identity": {
            "username": "nested-user",
            "factors": ["nested-password", {"otp": "654321"}],
        },
        "remember": True,
        "attempt": 2,
        "optional": None,
    }
    login_flow = RedteamLoginFlowConfig(payload=payload)

    serialized = login_flow.model_dump(mode="json")
    restored_payload = login_flow.to_internal().payload

    assert isinstance(login_flow.payload["identity"]["username"], SecretStr)
    assert isinstance(login_flow.payload["identity"]["factors"][0], SecretStr)
    assert isinstance(login_flow.payload["identity"]["factors"][1]["otp"], SecretStr)
    assert serialized["payload"] == {
        "identity": {
            "username": "**********",
            "factors": ["**********", {"otp": "**********"}],
        },
        "remember": True,
        "attempt": 2,
        "optional": None,
    }
    assert restored_payload == payload


def test_redteam_run_request_defaults_match_orchestrator_constructor():
    req = RedteamRunRequest(target_url="http://x")
    assert req.profile == "ci"
    assert req.concurrency == 5
    assert req.chat_path == "/chat"
    assert req.discovery_max_turns == 3
    assert req.suppress_spa_html_auth_bypass is True
    assert req.codegen_escalation_enabled is True


def test_redteam_run_result_json_roundtrip():
    result = RedteamRunResult(
        findings=[_finding()],
        scenario_records=[{"title": "s1"}],
        scan_outcome="high_findings",
        config_notes=["note"],
        token_usage=TokenUsage(input_tokens=1, output_tokens=2),
        resolved_chat_path="/api/chat",
        resolved_chat_path_source="sbom",
        health_report=TargetHealthReport(target_url="http://x", endpoint="/api/chat", run_id="r1"),
    )
    restored = RedteamRunResult.model_validate_json(result.model_dump_json())
    assert restored.findings[0].goal_type == "prompt_driven_threat"
    assert restored.scan_outcome == "high_findings"
    assert restored.health_report is not None
    assert restored.health_report.run_id == "r1"


def test_redteam_run_result_rejects_invalid_scan_outcome():
    with pytest.raises(ValidationError):
        RedteamRunResult(
            findings=[],
            scenario_records=[],
            scan_outcome="bogus",
            config_notes=[],
            token_usage=TokenUsage(input_tokens=0, output_tokens=0),
            resolved_chat_path="/c",
            resolved_chat_path_source="sbom",
        )


# ---------------------------------------------------------------------------
# run_redteam — thin-wrapper parity with RedteamOrchestrator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_redteam_constructs_orchestrator_and_builds_result():
    """The orchestrator receives the policy model unwrapped from a parse result."""
    findings = [_finding("prompt_driven_threat"), _finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)
    sbom = MagicMock()
    policy = CognitivePolicy(allowed_topics=["Support"])
    parsed_policy = CognitivePolicyParseResult(success=True, policy=policy)

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target", profile="standard")
        result = await run_redteam(request, sbom=sbom, policy=parsed_policy)

    mock_cls.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["sbom"] is sbom
    assert kwargs["policy"] is policy
    assert kwargs["target_url"] == "http://target"
    assert kwargs["profile"] == "standard"

    assert isinstance(result, RedteamRunResult)
    assert len(result.findings) == 2
    assert result.scenario_records == [dataclasses.asdict(mock_instance.scenario_records[0])]
    assert result.scan_outcome == "high_findings"
    assert result.config_notes == ["note1"]
    assert result.llm_executive_summary == "summary"
    # llm_coding_brief is computed by run_redteam itself (not read off the
    # orchestrator) and only populated when eval_llm is supplied — not the
    # case here.
    assert result.llm_coding_brief is None
    assert result.scenarios_run == 3
    assert result.token_usage.input_tokens == 10
    assert result.health_report is not None
    assert result.health_report.run_id == "r1"
    assert result.resolved_chat_path == "/api/chat"
    assert result.resolved_chat_path_source == "sbom"
    assert result.catalog_coverage is None
    assert result.coverage_tracker is not None
    assert result.coverage_tracker["nodes"][0]["name"] == "Agent1"


@pytest.mark.asyncio
async def test_run_redteam_normalizes_parse_result_for_remediation() -> None:
    """Remediation receives a CognitivePolicy, not the public parse envelope."""
    policy = CognitivePolicy(restricted_topics=["Credentials"])
    parsed_policy = CognitivePolicyParseResult(success=True, policy=policy)
    mock_instance = _make_mock_orchestrator([_finding()])

    with (
        patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls,
        patch(
            "nuguard.redteam.public_api._build_remediation_plan",
            new=AsyncMock(return_value=[]),
        ) as remediation_mock,
    ):
        mock_cls.return_value = mock_instance
        await run_redteam(
            RedteamRunRequest(target_url="http://target"),
            sbom=MagicMock(),
            policy=parsed_policy,
        )

    assert remediation_mock.await_args.kwargs["policy"] is policy


@pytest.mark.asyncio
async def test_run_redteam_passes_config_scalar_and_embedded_model_fields():
    """Every RedteamRunRequest field must reach the orchestrator constructor by
    attribute (never via model_dump(), which would serialize nested Pydantic
    config objects into plain dicts and break the orchestrator's constructor)."""
    mock_instance = _make_mock_orchestrator([])

    canary = CanaryConfig()
    auth = AuthConfig(type="none")
    triggers = RedteamFindingTriggers()

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(
            target_url="http://target",
            canary_config=canary,
            auth_config=auth,
            finding_triggers=triggers,
            guided_mutation_mode="soft",
            tree_breadth=2,
        )
        await run_redteam(request, sbom=MagicMock())

    _, kwargs = mock_cls.call_args
    assert kwargs["canary_config"] is canary
    assert isinstance(kwargs["auth_config"], AuthConfig)
    assert kwargs["auth_config"].model_dump() == auth.model_dump()
    assert kwargs["finding_triggers"] is triggers
    assert kwargs["guided_mutation_mode"] == "soft"
    assert kwargs["tree_breadth"] == 2


@pytest.mark.asyncio
async def test_run_redteam_reveals_secrets_only_for_orchestrator() -> None:
    """The public wrapper reveals protected values only at the runtime boundary."""
    mock_instance = _make_mock_orchestrator([])
    request = RedteamRunRequest(
        target_url="http://target",
        auth_config=RedteamAuthConfig(
            type="login_flow",
            login_flow=RedteamLoginFlowConfig(
                payload={"username": "runtime-user", "password": "runtime-password"}
            ),
        ),
        extra_headers={"X-API-Key": "runtime-header"},
        credentials={"pin": "runtime-pin"},
    )

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        await run_redteam(request, sbom=MagicMock())

    _, kwargs = mock_cls.call_args
    assert kwargs["auth_config"].login_flow.payload == {
        "username": "runtime-user",
        "password": "runtime-password",
    }
    assert kwargs["extra_headers"] == {"X-API-Key": "runtime-header"}
    assert kwargs["credentials"] == {"pin": "runtime-pin"}


@pytest.mark.asyncio
async def test_cli_orchestrator_adapter_protects_auth_values_before_public_call() -> None:
    """The CLI passes only secret-safe request values into the public API wrapper."""
    from nuguard.cli.commands.redteam import _run_orchestrator

    result = RedteamRunResult(
        findings=[],
        scenario_records=[],
        scan_outcome="no_findings",
        token_usage=TokenUsage(),
        resolved_chat_path="/chat",
        resolved_chat_path_source="configured",
    )
    auth = AuthConfig(
        type="login_flow",
        login_flow=LoginFlowConfig(
            payload={"identity": {"password": "cli-login-secret"}}
        ),
    )

    with patch(
        "nuguard.redteam.public_api.run_redteam",
        new=AsyncMock(return_value=result),
    ) as run_mock:
        await _run_orchestrator(
            sbom_doc=object(),
            target_url="http://target",
            cognitive_policy=None,
            canary_config=None,
            profile="ci",
            min_impact_score=0.0,
            scenario_filter=None,
            auth_config=auth,
            extra_headers={"X-API-Key": "cli-header-secret"},
            credentials={"pin": "cli-credential-secret"},
        )

    request = run_mock.await_args.args[0]
    assert isinstance(request.auth_config, RedteamAuthConfig)
    assert isinstance(request.auth_config.login_flow.payload["identity"]["password"], SecretStr)
    assert isinstance(request.extra_headers["X-API-Key"], SecretStr)
    assert isinstance(request.credentials["pin"], SecretStr)
    serialized = request.model_dump_json()
    assert "cli-login-secret" not in serialized
    assert "cli-header-secret" not in serialized
    assert "cli-credential-secret" not in serialized


@pytest.mark.asyncio
async def test_run_redteam_applies_scenario_filter_post_run():
    findings = [_finding("prompt_driven_threat"), _finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target", scenario_filter=["prompt-driven"])
        result = await run_redteam(request, sbom=MagicMock())

    assert len(result.findings) == 1
    assert result.findings[0].goal_type == "prompt_driven_threat"


@pytest.mark.asyncio
async def test_run_redteam_scenario_type_level_filter_does_not_drop_finding():
    """Regression test for the bug where a scenario-type-level filter (e.g.
    "APPROVAL_STATE_FORGERY") correctly selected which scenario ran, via the
    orchestrator's own pre-run filter, but then had its resulting finding
    silently dropped by this post-run re-check — because the re-check only
    ever compared the filter token against goal_type, never scenario_type."""
    findings = [
        _finding("prompt_driven_threat", scenario_type="APPROVAL_STATE_FORGERY"),
        _finding("data_exfiltration", scenario_type="DIRECT_PII_EXTRACTION"),
    ]
    mock_instance = _make_mock_orchestrator(findings)

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target", scenario_filter=["APPROVAL_STATE_FORGERY"])
        result = await run_redteam(request, sbom=MagicMock())

    assert len(result.findings) == 1
    assert result.findings[0].scenario_type == "APPROVAL_STATE_FORGERY"


@pytest.mark.asyncio
async def test_run_redteam_no_scenario_filter_keeps_all_findings():
    findings = [_finding("prompt_driven_threat"), _finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target")
        result = await run_redteam(request, sbom=MagicMock())

    assert len(result.findings) == 2


@pytest.mark.asyncio
async def test_run_redteam_stream_emits_terminal_and_final_result(monkeypatch):
    expected = RedteamRunResult(
        findings=[_finding()],
        scenario_records=[{"title": "s1"}],
        scan_outcome="high_findings",
        config_notes=[],
        token_usage=TokenUsage(input_tokens=1, output_tokens=2),
        resolved_chat_path="/chat",
        resolved_chat_path_source="config",
        scenarios_run=1,
    )

    async def _fake_run(*args, **kwargs):
        return expected

    monkeypatch.setattr("nuguard.redteam.public_api.run_redteam", _fake_run)

    handle = await run_redteam_stream(RedteamRunRequest(target_url="http://target"), sbom=MagicMock())
    events = [event async for event in handle.events]
    final = await handle.final_result()

    assert events[0].event_type == "run_started"
    assert events[-1].event_type == "completed"
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert final.scenarios_run == 1
    assert len(final.findings) == 1


@pytest.mark.asyncio
async def test_run_redteam_propagates_exceptions():
    """A full scan is not best-effort — real exceptions must not be swallowed."""
    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value.run = AsyncMock(side_effect=RuntimeError("target unreachable"))
        request = RedteamRunRequest(target_url="http://target")
        with pytest.raises(RuntimeError, match="target unreachable"):
            await run_redteam(request, sbom=MagicMock())


# ---------------------------------------------------------------------------
# remediation_plan — structured, machine-actionable remediation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_redteam_populates_remediation_plan():
    """Findings + a real SBOM should produce structured RemediationArtefact objects,
    via the same RemediationSynthesizer the CLI uses for its report's remediation plan."""
    from types import SimpleNamespace

    from nuguard.remediation.models import RemediationArtefact, RemediationArtefactType

    findings = [_finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)
    fake_artefact = RemediationArtefact(
        finding_ids=["f1"],
        component="unknown",
        component_type="AGENT",
        artefact_type=RemediationArtefactType.OUTPUT_GUARDRAIL,
        priority="high",
        rationale="d",
    )

    with (
        patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls,
        patch(
            "nuguard.remediation.synthesizer.RemediationSynthesizer.synthesize_findings_async"
        ) as mock_synth,
    ):
        mock_cls.return_value = mock_instance
        mock_synth.return_value = [fake_artefact]
        request = RedteamRunRequest(target_url="http://target")
        result = await run_redteam(request, sbom=SimpleNamespace(nodes=[], edges=[]))

    mock_synth.assert_awaited_once()
    (finding_dicts,), _ = mock_synth.await_args
    assert finding_dicts[0]["finding_id"] == "f1"
    assert finding_dicts[0]["goal_type"] == "data_exfiltration"
    assert result.remediation_plan == [fake_artefact]
    # Finding.remediation is backfilled from the matching artefact's rationale.
    assert result.findings[0].remediation == "d"


@pytest.mark.asyncio
async def test_run_redteam_remediation_plan_empty_without_findings():
    mock_instance = _make_mock_orchestrator([])

    with patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls:
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target")
        result = await run_redteam(request, sbom=MagicMock())

    assert result.remediation_plan == []


@pytest.mark.asyncio
async def test_run_redteam_remediation_synthesis_failure_is_swallowed():
    """Remediation synthesis is best-effort — a failure must not fail the run."""
    findings = [_finding("data_exfiltration")]
    mock_instance = _make_mock_orchestrator(findings)

    with (
        patch("nuguard.redteam.public_api.RedteamOrchestrator") as mock_cls,
        patch(
            "nuguard.remediation.synthesizer.RemediationSynthesizer.synthesize_findings_async",
            side_effect=RuntimeError("boom"),
        ),
    ):
        mock_cls.return_value = mock_instance
        request = RedteamRunRequest(target_url="http://target")
        from types import SimpleNamespace

        result = await run_redteam(request, sbom=SimpleNamespace(nodes=[], edges=[]))

    assert result.remediation_plan == []
    assert len(result.findings) == 1
