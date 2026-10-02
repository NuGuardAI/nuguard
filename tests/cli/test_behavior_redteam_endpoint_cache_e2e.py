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


def _write_one_node_sbom(tmp_path: Path) -> Path:
    doc = AiSbomDocument(
        target="./test-app",
        nodes=[
            Node(
                name="chat_endpoint",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=NodeMetadata(endpoint=ENDPOINT, method="POST", chat_payload_key="message"),
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
