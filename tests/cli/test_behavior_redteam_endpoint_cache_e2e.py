"""End-to-end CLI tests for issue #611 Phase 2/3 caching through the real
``nuguard behavior`` and ``nuguard redteam`` entry points — not direct
BehaviorRunner/RedteamOrchestrator method calls (already covered exhaustively
elsewhere), but the actual CLI wiring: SBOM loading -> enrich_sbom_for_run ->
runner/orchestrator construction -> cache check.

Mirrors tests/cli/test_target_verify_discovery.py's
test_verify_run_twice_reuses_cached_endpoint_and_profile, which proved this
same pattern for `target verify` after finding (and fixing) two real CLI
wiring bugs there. behavior.py/redteam.py already call enrich_sbom_for_run
(confirmed by reading the source before writing these), so these tests exist
to actually prove that wiring works, not assume it from the code.

Keeps the run minimal: a one-node SBOM (a single API_ENDPOINT, no AGENT/TOOL
nodes) so scenario generation produces nothing to attack and the run
completes almost immediately after bootstrap/discovery, the same trick
already used throughout nuguard/redteam/tests/test_orchestrator_*.py.
"""
from __future__ import annotations

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

_ACCOUNT_RESPONSE = (
    "Account holder: Alice Johnson. Your account number is ACCT-0001, "
    "current balance $4,210.55."
)


def _write_one_node_sbom(tmp_path: Path, payload_key: str = "message") -> Path:
    doc = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(endpoint=ENDPOINT, method="POST", chat_payload_key=payload_key),
            ),
        ],
    )
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    return sbom_path


@respx.mock
def test_behavior_run_twice_reuses_cached_endpoint(tmp_path: Path) -> None:
    sbom_path = _write_one_node_sbom(tmp_path)
    route = respx.post(FULL_URL).mock(
        return_value=httpx.Response(200, json={"response": _ACCOUNT_RESPONSE})
    )
    out_path = tmp_path / "behavior-report.md"

    args = [
        "behavior",
        "--dynamic",
        "--target", TARGET,
        "--sbom", str(sbom_path),
        "--output", str(out_path),
    ]
    first = runner.invoke(app, args, catch_exceptions=False)
    assert first.exit_code == 0, first.output
    calls_after_first_run = route.call_count
    assert calls_after_first_run > 0

    second = runner.invoke(app, args, catch_exceptions=False)

    assert second.exit_code == 0, second.output
    new_calls = route.call_count - calls_after_first_run
    assert new_calls < calls_after_first_run, (
        f"Expected the second run to make fewer live calls than the first "
        f"(cold={calls_after_first_run}, second-run-new={new_calls}) — "
        f"resolved_chat_endpoint should have been reused from the enriched SBOM."
    )
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()
    import json

    data = json.loads(enriched_path.read_text())
    assert data.get("resolved_chat_endpoint", {}).get("chat_path") == ENDPOINT


@respx.mock
def test_redteam_run_twice_reuses_cached_endpoint(tmp_path: Path) -> None:
    sbom_path = _write_one_node_sbom(tmp_path)
    route = respx.post(FULL_URL).mock(
        return_value=httpx.Response(200, json={"response": _ACCOUNT_RESPONSE})
    )
    out_path = tmp_path / "redteam-report.md"

    args = [
        "redteam",
        "--target", TARGET,
        "--sbom", str(sbom_path),
        "--output", str(out_path),
    ]
    first = runner.invoke(app, args, catch_exceptions=False)
    assert first.exit_code == 0, first.output
    calls_after_first_run = route.call_count
    assert calls_after_first_run > 0

    second = runner.invoke(app, args, catch_exceptions=False)

    assert second.exit_code == 0, second.output
    new_calls = route.call_count - calls_after_first_run
    assert new_calls < calls_after_first_run, (
        f"Expected the second run to make fewer live calls than the first "
        f"(cold={calls_after_first_run}, second-run-new={new_calls}) — "
        f"resolved_chat_endpoint should have been reused from the enriched SBOM."
    )
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()
    import json

    data = json.loads(enriched_path.read_text())
    assert data.get("resolved_chat_endpoint", {}).get("chat_path") == ENDPOINT


# ---------------------------------------------------------------------------
# Cross-COMMAND E2E (as opposed to cross-tool-via-direct-function-call,
# already covered by tests/cli/test_target_verify_endpoint_cache.py): a real
# `nuguard target verify` CliRunner invocation, followed by a real, separate
# `nuguard behavior`/`nuguard redteam` CliRunner invocation against the same
# --sbom file, proving the actual end-user workflow this issue is about
# works through both commands' full CLI entry points — not just that the
# underlying functions hand off correctly when called directly.
# ---------------------------------------------------------------------------


def _verify_args(sbom_path: Path) -> list[str]:
    return ["target", "verify", "--target", TARGET, "--endpoint", ENDPOINT, "--sbom", str(sbom_path)]


@respx.mock
@pytest.mark.parametrize("payload_key", ["message", "text"])
def test_target_verify_then_behavior_reuses_cached_endpoint(tmp_path: Path, payload_key: str) -> None:
    sbom_path = _write_one_node_sbom(tmp_path, payload_key)
    reply = {"outputs": [{"text": _ACCOUNT_RESPONSE}]} if payload_key == "text" else {"response": _ACCOUNT_RESPONSE}
    route = respx.post(FULL_URL).mock(
        return_value=httpx.Response(200, json=reply)
    )

    verify_result = runner.invoke(app, _verify_args(sbom_path), catch_exceptions=False)
    assert verify_result.exit_code == 0, verify_result.output
    calls_after_verify = route.call_count
    assert calls_after_verify > 0
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()

    out_path = tmp_path / "behavior-report.md"
    behavior_result = runner.invoke(
        app,
        [
            "behavior", "--dynamic",
            "--target", TARGET,
            "--sbom", str(sbom_path),
            "--output", str(out_path),
        ],
        catch_exceptions=False,
    )

    assert behavior_result.exit_code == 0, behavior_result.output
    # Not a call-count comparison here (unlike the redteam/target-verify pair
    # below): behavior has no --endpoint CLI flag, so this run is always
    # non-explicit, and develop's resolve_target_session now does its own
    # live endpoint validation *during session resolution* — upstream of,
    # and independent of, this Phase 3 cache — every time no endpoint is
    # explicitly configured. That upstream stage's own live calls make raw
    # call counts an unreliable signal here; the cache-hit message is the
    # direct, reliable proof that _ensure_endpoint_preflight's own later
    # validation stage was actually skipped in favor of the cached result.
    assert "reused from a previously-validated SBOM resolution" in behavior_result.output, (
        "Expected behavior to report reusing target verify's cached endpoint "
        f"resolution. Output:\n{behavior_result.output}"
    )


@respx.mock
@pytest.mark.parametrize("payload_key", ["message", "text"])
def test_target_verify_then_redteam_reuses_cached_endpoint(tmp_path: Path, payload_key: str) -> None:
    sbom_path = _write_one_node_sbom(tmp_path, payload_key)
    reply = {"outputs": [{"text": _ACCOUNT_RESPONSE}]} if payload_key == "text" else {"response": _ACCOUNT_RESPONSE}
    route = respx.post(FULL_URL).mock(
        return_value=httpx.Response(200, json=reply)
    )

    verify_result = runner.invoke(app, _verify_args(sbom_path), catch_exceptions=False)
    assert verify_result.exit_code == 0, verify_result.output
    calls_after_verify = route.call_count
    assert calls_after_verify > 0
    enriched_path = sbom_path.with_name("app.sbom.enriched.json")
    assert enriched_path.exists()

    out_path = tmp_path / "redteam-report.md"
    redteam_result = runner.invoke(
        app,
        [
            "redteam",
            "--target", TARGET,
            "--sbom", str(sbom_path),
            "--output", str(out_path),
        ],
        catch_exceptions=False,
    )

    assert redteam_result.exit_code == 0, redteam_result.output
    new_calls = route.call_count - calls_after_verify
    assert new_calls < calls_after_verify, (
        f"Expected `redteam` to make fewer live calls than `target verify`'s cold "
        f"run (verify={calls_after_verify}, redteam-new={new_calls}) — it should "
        f"have reused target verify's cached endpoint resolution from the enriched SBOM."
    )
