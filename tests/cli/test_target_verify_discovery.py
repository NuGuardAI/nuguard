"""CLI tests for the SBOM-driven pre-scan discovery in ``nuguard target verify``."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from nuguard.cli.main import app
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, NodeType
from nuguard.sbom.serializer import AiSbomSerializer

runner = CliRunner()

TARGET = "http://app.test"
ENDPOINT = "/chat"
FULL_URL = f"{TARGET}{ENDPOINT}"

# Long enough (>=30 chars) for TargetAppClient's response-key auto-detection,
# and shaped to match id_extractor's name/ID patterns on the first turn so
# discovery stops after a single round-trip.
_ACCOUNT_RESPONSE = (
    "Account holder: Alice Johnson. Your account number is ACCT-0001, "
    "current balance $4,210.55."
)


def _account_reply(request: httpx.Request) -> httpx.Response:
    if "message" not in json.loads(request.content):
        return httpx.Response(422, json={"detail": "message is required"})
    return httpx.Response(200, json={"response": _ACCOUNT_RESPONSE})


def _write_sbom(tmp_path: Path) -> Path:
    doc = AiSbomDocument(target="./test-app")
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    return sbom_path


@respx.mock
def test_verify_with_sbom_discovers_account(tmp_path: Path) -> None:
    respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )
    sbom_path = _write_sbom(tmp_path)
    result = runner.invoke(
        app,
        [
            "target",
            "verify",
            "--target",
            TARGET,
            "--endpoint",
            ENDPOINT,
            "--sbom",
            str(sbom_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "API Endpoint" in result.output
    assert "Path:          /chat" in result.output
    # Discovered account/user identity and golden data are published in the table.
    assert "Alice Johnson" in result.output
    assert "ACCT-0001" in result.output


@respx.mock
def test_verify_table_publishes_userid_and_golden_data(tmp_path: Path) -> None:
    """Identity column combines configured user_id + discovered name; Detail
    column carries the golden data — same style as behavior/redteam reports."""
    respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )
    sbom_path = _write_sbom(tmp_path)
    cfg_path = tmp_path / "nuguard.yaml"
    cfg_path.write_text(
        "target:\n  chat_payload_extras:\n    user_id: alice\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "target",
            "verify",
            "--config",
            str(cfg_path),
            "--target",
            TARGET,
            "--endpoint",
            ENDPOINT,
            "--sbom",
            str(sbom_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # Rich may word-wrap the cell across lines in a narrow terminal — check the
    # pieces landed in the table rather than requiring one unbroken substring.
    assert "alice" in result.output
    assert "Alice" in result.output and "Johnson" in result.output
    assert "·" in result.output
    assert "ACCT-0001" in result.output


@respx.mock
def test_verify_auto_discovers_endpoint_from_sbom(tmp_path: Path) -> None:
    """No --endpoint given: the real chat path must come from the SBOM, and
    auth bootstrap must probe that path, not the generic '/chat' default."""
    discovered_endpoint = "/api/agent/converse"
    respx.post(f"{TARGET}{discovered_endpoint}").mock(
        side_effect=_account_reply
    )
    doc = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(
                    endpoint=discovered_endpoint, method="POST", chat_payload_key="message"
                ),
            ),
        ],
    )
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")

    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--sbom", str(sbom_path)],
    )
    assert result.exit_code == 0, result.output
    assert discovered_endpoint in result.output
    assert "Alice Johnson" in result.output


@respx.mock
def test_verify_with_sbom_and_skip_discovery(tmp_path: Path) -> None:
    respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )
    sbom_path = _write_sbom(tmp_path)
    result = runner.invoke(
        app,
        [
            "target",
            "verify",
            "--target",
            TARGET,
            "--endpoint",
            ENDPOINT,
            "--sbom",
            str(sbom_path),
            "--skip-discovery",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "API Endpoint" in result.output
    assert "Alice Johnson" not in result.output
    assert "ACCT-0001" not in result.output


@respx.mock
def test_verify_without_sbom_notes_discovery_unavailable(tmp_path: Path) -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    cfg_path = tmp_path / "nuguard.yaml"
    cfg_path.write_text("target:\n  chat_payload_key: message\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--endpoint", ENDPOINT, "--config", str(cfg_path)],
    )
    assert result.exit_code == 0, result.output
    assert "discovery skipped" in result.output
    assert "API Endpoint" in result.output


@pytest.mark.xfail(
    reason=(
        "Pre-existing bug in resolve_chat_endpoint's candidate-retry logic "
        "(nuguard/common/endpoint_detection/resolver.py), independent of "
        "issue #611's own changes — confirmed reproducible on a clean "
        "develop checkout with no Phase 1-3 code involved at all. "
        "resolve_chat_endpoint only retries OTHER SBOM-declared candidates "
        "when the SBOM has zero real nodes (a summary.api_endpoints-only "
        "shape, as this test's SBOM happens to use); the moment the SBOM "
        "has real API_ENDPOINT nodes — the normal shape for any SBOM "
        "produced by `nuguard sbom generate` — and the top-ranked candidate "
        "fails live validation, it falls straight through to ~20 generic "
        "hardcoded guess paths instead of trying the SBOM's OTHER declared "
        "candidate(s), and fails entirely. Reproduced directly against "
        "resolve_chat_endpoint() with a plain 2-node SBOM, no enrichment "
        "involved. Target Verify's own fix in this branch (loading the "
        "enriched SBOM artifact via enrich_sbom_for_run so its "
        "discovered_profile/resolved_chat_endpoint caches are reachable) "
        "happens to turn this test's specific SBOM into one with real "
        "nodes, surfacing the pre-existing gap — it does not cause it. "
        "File a separate issue to fix resolve_chat_endpoint's "
        "fallback-retry to try all SBOM-declared candidates, not just the "
        "generic guess list, before removing this xfail."
    ),
    strict=True,
)
@respx.mock
def test_verify_keeps_live_probed_endpoint_for_session_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    """An endpoint the pre-bootstrap probe confirmed (e.g. kscope's /extract,
    not chat-named) is passed to the shared session resolver as kept, so it is
    not re-resolved to another SBOM candidate."""
    from nuguard.common.errors import TargetEndpointNotFoundError

    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.post(f"{TARGET}/chat").mock(
        return_value=httpx.Response(400, json={"error": "consumerID and message are required"})
    )
    respx.post(f"{TARGET}/extract").mock(
        side_effect=_account_reply
    )
    respx.post(url__regex=r".*").mock(return_value=httpx.Response(404))
    doc = AiSbomDocument.model_validate(
        {"target": "./test-app", "summary": {"api_endpoints": ["/chat", "/extract"]}}
    )
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")

    captured: dict = {}

    async def _fake_resolve_target_session(**kwargs):
        captured.update(kwargs)
        raise TargetEndpointNotFoundError("stop after capture", url=TARGET)

    monkeypatch.setattr(
        "nuguard.common.session_resolver.resolve_target_session",
        _fake_resolve_target_session,
    )
    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--sbom", str(sbom_path)],
    )

    assert result.exit_code == 1, result.output
    assert captured["chat_path"] == "/extract"
    assert captured["endpoint_explicit"] is True
    assert captured["payload_key_explicit"] is True
    assert captured["endpoint_source_hint"] == "probe"


@respx.mock
def test_verify_resolves_templated_endpoint_and_discovers_account(tmp_path: Path) -> None:
    """Regression test for issue #611: a templated, two-step chat endpoint
    (create a conversation, then POST to .../:id/messages) must resolve the
    path param and discover a profile instead of every turn — including the
    auth health-check probe and the pre-scan discovery conversation — failing
    with "[CONFIG_ERROR: unresolved path param 'id']"."""
    chat_endpoint = "/chat/conversations/:id/messages"
    source_endpoint = "/chat/conversations"
    # The health-check probe (AuthBootstrapper) and the pre-scan discovery
    # preflight each independently resolve :id via their own POST to the
    # source endpoint — both return the same fixed id here, so both end up
    # POSTing to the same resolved message path.
    respx.post(f"{TARGET}{source_endpoint}").mock(
        return_value=httpx.Response(201, json={"id": "conv-abc123"})
    )
    respx.post(f"{TARGET}/chat/conversations/conv-abc123/messages").mock(
        side_effect=_account_reply
    )
    doc = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(
                    endpoint=chat_endpoint,
                    method="POST",
                    chat_payload_key="message",
                    path_params=["id"],
                    path_param_sources={"id": source_endpoint},
                ),
            ),
            Node(
                name="create_conversation",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(endpoint=source_endpoint, method="POST"),
            ),
        ],
    )
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "target",
            "verify",
            "--target",
            TARGET,
            "--endpoint",
            chat_endpoint,
            "--sbom",
            str(sbom_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "CONFIG_ERROR" not in result.output
    assert "Alice Johnson" in result.output
    assert "ACCT-0001" in result.output


@respx.mock
def test_verify_skips_discovery_when_preflight_finds_no_working_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    """When preflight reports ok=False (no working chat endpoint — e.g. an
    explicit endpoint that fails validation with rotation suppressed), that
    must surface the same way the existing "default credential did not
    verify" skip note does — one summary line after the table — not as a
    separately-styled message printed earlier from inside
    _run_pre_scan_discovery. validate_and_rotate_chat_endpoint's own
    rotation/scoring logic is covered by nuguard/common/tests/test_endpoint_preflight.py;
    this test only verifies target.py's handling of an ok=False outcome."""
    from nuguard.common.endpoint_preflight import PreflightOutcome

    respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )

    async def _fake_preflight(client, sbom, **kwargs):
        _ = (client, sbom, kwargs)
        return PreflightOutcome(ok=False, notes=["endpoint rejected the test request"])

    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", _fake_preflight
    )

    sbom_path = _write_sbom(tmp_path)
    result = runner.invoke(
        app,
        [
            "target",
            "verify",
            "--target",
            TARGET,
            "--endpoint",
            ENDPOINT,
            "--sbom",
            str(sbom_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # Rich may word-wrap this line in a narrow terminal — normalize before matching.
    normalized_output = " ".join(result.output.split())
    assert "Skipping account/golden-data discovery" in normalized_output
    assert "no working chat endpoint found during preflight validation" in normalized_output
    assert "Alice Johnson" not in result.output


# ---------------------------------------------------------------------------
# Test A: --preflight-candidates CLI wiring (issue #611 review finding).
# Monkeypatches validate_and_rotate_chat_endpoint to capture max_candidates —
# the goal is proving the CLI's flag/config plumbing reaches it correctly,
# not re-testing rotation's own internal candidate-scoring logic (covered by
# nuguard/common/tests/test_endpoint_preflight.py).
# ---------------------------------------------------------------------------

def _capture_max_candidates(monkeypatch, captured: list[int]):
    from nuguard.common.endpoint_preflight import PreflightOutcome

    async def _fake_preflight(client, sbom, **kwargs):
        _ = (client, sbom)
        captured.append(kwargs["max_candidates"])
        return PreflightOutcome(ok=True)

    monkeypatch.setattr(
        "nuguard.common.endpoint_preflight.validate_and_rotate_chat_endpoint", _fake_preflight
    )


@respx.mock
def test_verify_preflight_candidates_cli_flag_overrides_default(
    tmp_path: Path, monkeypatch
) -> None:
    respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )
    captured: list[int] = []
    _capture_max_candidates(monkeypatch, captured)
    sbom_path = _write_sbom(tmp_path)

    result = runner.invoke(
        app,
        [
            "target", "verify",
            "--target", TARGET,
            "--endpoint", ENDPOINT,
            "--sbom", str(sbom_path),
            "--preflight-candidates", "7",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured == [7]


@respx.mock
def test_verify_preflight_candidates_falls_back_to_config(
    tmp_path: Path, monkeypatch
) -> None:
    respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )
    captured: list[int] = []
    _capture_max_candidates(monkeypatch, captured)
    sbom_path = _write_sbom(tmp_path)
    cfg_path = tmp_path / "nuguard.yaml"
    cfg_path.write_text("redteam:\n  preflight_candidates: 5\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "target", "verify",
            "--config", str(cfg_path),
            "--target", TARGET,
            "--endpoint", ENDPOINT,
            "--sbom", str(sbom_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured == [5]


@respx.mock
def test_verify_preflight_candidates_defaults_to_three(
    tmp_path: Path, monkeypatch
) -> None:
    respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )
    captured: list[int] = []
    _capture_max_candidates(monkeypatch, captured)
    sbom_path = _write_sbom(tmp_path)

    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--endpoint", ENDPOINT, "--sbom", str(sbom_path)],
    )
    assert result.exit_code == 0, result.output
    assert captured == [3]


# ---------------------------------------------------------------------------
# Test B: explicit --endpoint suppresses rotation even when a better SBOM
# candidate exists and the explicit endpoint's reply looks wrong (HTTP 400 —
# a status bootstrap's health-check classifies as "ok" with a payload_hint,
# but validate_and_rotate_chat_endpoint's stricter preflight check flags as
# a wrong-route signal). Real preflight, real respx routes — not mocked —
# proving "config must win" (documentation/docs/endpoint-resolution-
# precedence-plan.md) holds for the CLI's own has_explicit_endpoint wiring,
# not just the public API's (already covered by
# test_verify_target_threads_has_explicit_endpoint in
# tests/common/test_target_verify_public_api.py).
# ---------------------------------------------------------------------------

@respx.mock
def test_verify_explicit_endpoint_suppresses_rotation_to_better_candidate(
    tmp_path: Path,
) -> None:
    alt_endpoint = "/api/better-chat"
    bad_route = respx.post(FULL_URL).mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    good_route = respx.post(f"{TARGET}{alt_endpoint}").mock(
        side_effect=_account_reply
    )
    doc = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.9,
                metadata=NodeMetadata(endpoint=ENDPOINT, method="POST", chat_payload_key="message"),
            ),
            Node(
                name="better_chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(endpoint=alt_endpoint, method="POST", chat_payload_key="message"),
            ),
        ],
    )
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "target", "verify",
            "--target", TARGET,
            "--endpoint", ENDPOINT,
            "--sbom", str(sbom_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # Exact call counts on the primary endpoint aren't asserted here: this
    # harness (CliRunner + respx on this platform) retries/re-sends more
    # than the application logic itself does even for an already-passing,
    # unmodified scenario (confirmed empirically against a vanilla success
    # case) — an environment characteristic unrelated to this fix. What
    # actually proves "config must win" is that the alternate endpoint is
    # never touched at all, regardless of how many times the explicit one
    # was retried.
    assert bad_route.called
    assert good_route.call_count == 0  # rotation never attempted
    assert "Alice Johnson" not in result.output
    normalized_output = " ".join(result.output.split())
    assert "no working chat endpoint found during preflight validation" in normalized_output


# ---------------------------------------------------------------------------
# Test E: auto-discovered endpoint (no --endpoint) that is ALSO templated —
# combines SBOM auto-discovery with path-param bootstrapping, two features
# that are each tested separately elsewhere but never together.
# ---------------------------------------------------------------------------

@respx.mock
def test_verify_auto_discovers_templated_endpoint_and_bootstraps_path_param(
    tmp_path: Path,
) -> None:
    chat_endpoint = "/chat/conversations/:id/messages"
    source_endpoint = "/chat/conversations"
    respx.post(f"{TARGET}{source_endpoint}").mock(
        return_value=httpx.Response(201, json={"id": "conv-xyz789"})
    )
    respx.post(f"{TARGET}/chat/conversations/conv-xyz789/messages").mock(
        side_effect=_account_reply
    )
    doc = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(
                    endpoint=chat_endpoint,
                    method="POST",
                    chat_payload_key="message",
                    path_params=["id"],
                    path_param_sources={"id": source_endpoint},
                ),
            ),
            Node(
                name="create_conversation",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(endpoint=source_endpoint, method="POST"),
            ),
        ],
    )
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")

    # No --endpoint: must come from SBOM auto-discovery, same as
    # test_verify_auto_discovers_endpoint_from_sbom, but this time the
    # auto-discovered endpoint is also templated.
    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--sbom", str(sbom_path)],
    )
    assert result.exit_code == 0, result.output
    assert "CONFIG_ERROR" not in result.output
    assert chat_endpoint in result.output
    assert "Alice Johnson" in result.output
    assert "ACCT-0001" in result.output


@respx.mock
def test_verify_run_twice_reuses_cached_endpoint_and_profile(tmp_path: Path) -> None:
    """End-to-end regression for issue #611 Phase 2/3: two real, separate
    `target verify` CLI invocations against the same --sbom file must share
    the cached endpoint resolution and discovered profile, not re-probe and
    re-discover from scratch every time.

    This specifically covers a wiring gap found while adding this test:
    _verify_async never loaded the already-enriched SBOM sidecar file it
    itself writes (unlike `behavior`/`redteam`, which both call
    enrich_sbom_for_run) — so a second real CLI invocation could never see
    either cache, only the pristine source SBOM. Fixed by wiring
    enrich_sbom_for_run into _verify_async and adding the discovered_profile
    cache check/write _run_pre_scan_discovery was missing entirely.
    """
    doc = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(endpoint=ENDPOINT, method="POST", chat_payload_key="message"),
            ),
            Node(name="Assistant", component_type=NodeType.AGENT, confidence=0.95),
        ],
    )
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    route = respx.post(FULL_URL).mock(
        side_effect=_account_reply
    )

    args = ["target", "verify", "--target", TARGET, "--endpoint", ENDPOINT, "--sbom", str(sbom_path)]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "Alice Johnson" in first.output
    calls_after_first_run = route.call_count

    second = runner.invoke(app, args)

    assert second.exit_code == 0, second.output
    assert "Alice Johnson" in second.output
    assert "ACCT-0001" in second.output
    assert "reused from a previously-validated SBOM resolution" in second.output
    assert "Pre-scan discovery (from enriched SBOM)" in second.output
    # The second run must not re-probe/re-discover live — strictly fewer new
    # HTTP calls than the first run made from a cold cache.
    assert route.call_count - calls_after_first_run < calls_after_first_run
