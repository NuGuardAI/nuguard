"""CLI tests for the SBOM-driven pre-scan discovery in ``nuguard target verify``."""
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

# Long enough (>=30 chars) for TargetAppClient's response-key auto-detection,
# and shaped to match id_extractor's name/ID patterns on the first turn so
# discovery stops after a single round-trip.
_ACCOUNT_RESPONSE = (
    "Account holder: Alice Johnson. Your account number is ACCT-0001, "
    "current balance $4,210.55."
)


def _write_sbom(tmp_path: Path) -> Path:
    doc = AiSbomDocument(target="./test-app")
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
    return sbom_path


@respx.mock
def test_verify_with_sbom_discovers_account(tmp_path: Path) -> None:
    respx.post(FULL_URL).mock(
        return_value=httpx.Response(200, json={"response": _ACCOUNT_RESPONSE})
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
        return_value=httpx.Response(200, json={"response": _ACCOUNT_RESPONSE})
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
        return_value=httpx.Response(200, json={"response": _ACCOUNT_RESPONSE})
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
        return_value=httpx.Response(200, json={"response": _ACCOUNT_RESPONSE})
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
def test_verify_without_sbom_notes_discovery_unavailable() -> None:
    respx.post(FULL_URL).mock(return_value=httpx.Response(200))
    result = runner.invoke(
        app,
        ["target", "verify", "--target", TARGET, "--endpoint", ENDPOINT],
    )
    assert result.exit_code == 0, result.output
    assert "discovery skipped" in result.output
    assert "API Endpoint" in result.output
