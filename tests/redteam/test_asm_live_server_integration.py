"""End-to-end integration test for W1 (ASM) and W10 (dual-path) against a
real local server — not mocked httpx, a genuine TCP listener and a real
HTTP/1.1 response parse.

This intentionally does NOT add a new vendored fixture app under
tests/apps/ (those are full third-party applications used for broader
sbom-generation/CLI e2e coverage — a much heavier convention than a single
unit test needs). Instead it follows the same pattern already used by
tests/redteam/test_callback_canary.py: a minimal hand-rolled asyncio server
defined directly in the test, with zero new dependencies (no fastapi/flask/
uvicorn — none of those are installed; nuguard's own SBOM adapters only
ever *statically parse* that kind of source, they never need the framework
importable). This is deliberately a smaller, more targeted answer to "needs
a fixture app" than a new tests/apps/ directory would be.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from nuguard.redteam.enrichment.asm_prober import build_asm, build_asm_findings
from nuguard.redteam.scenarios.dual_path import compare_dual_path
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument


class _LiveFixtureServer:
    """Serves the exact unauthenticated-surface shape the W1/W10 findings
    are meant to catch: an open tool inventory, an open OpenAPI schema, a
    chat endpoint that refuses direct account requests, and a sibling
    /api/accounts/{id} REST route that answers them anyway (no gate)."""

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None
        self.host = "127.0.0.1"
        self.port = 0

    async def __aenter__(self) -> "_LiveFixtureServer":
        self._server = await asyncio.start_server(self._handle, host=self.host, port=0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            request = data.decode("utf-8", errors="replace")
            first_line = request.split("\r\n")[0]
            parts = first_line.split(" ")
            method, path = (parts[0], parts[1].split("?")[0]) if len(parts) >= 2 else ("GET", "/")

            status, body = self._route(method, path, request)
            body_bytes = body.encode("utf-8")
            response = (
                f"HTTP/1.1 {status} X\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body_bytes)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8") + body_bytes
            writer.write(response)
            await writer.drain()
        except Exception:  # noqa: BLE001 — best-effort test server
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _route(method: str, path: str, request: str) -> tuple[int, str]:
        if path == "/openapi.json":
            return 200, json.dumps({"paths": {"/api/accounts/{id}": {}}})
        if path == "/api/tools":
            return 200, json.dumps({"tools": ["grant_admin_role", "transfer_funds"]})
        if path == "/api/accounts/1":
            return 200, json.dumps({"account_id": 1, "balance": 1000, "email": "a@example.com"})
        if path == "/chat" and method == "POST":
            return 200, json.dumps({"response": "I cannot share another account's details."})
        return 404, json.dumps({"error": "not found"})


@pytest.mark.asyncio
async def test_asm_and_dual_path_against_a_real_live_server() -> None:
    async with _LiveFixtureServer() as fixture:
        base_url = f"http://{fixture.host}:{fixture.port}"
        sbom = AiSbomDocument(target="live-fixture", nodes=[], edges=[])
        client = TargetAppClient(base_url=base_url, chat_path="/chat", timeout=5.0)
        async with client:
            # W1: ASM finds the open inventory + schema endpoints over a real
            # TCP connection and HTTP/1.1 response parse — not mocked httpx.
            asm = await build_asm(client, sbom, max_probe_requests=25)
            findings = build_asm_findings(asm)
            finding_titles = "\n".join(f.title for f in findings)

            assert any(e.url == "/api/tools" and e.auth_classification == "open" for e in asm.endpoints)
            assert any(e.url == "/openapi.json" and e.auth_classification == "open" for e in asm.endpoints)
            assert "/api/tools" in finding_titles
            assert "/openapi.json" in finding_titles

            # W10: chat refuses, direct REST returns the same data — gate_bypass.
            session = AttackSession(session_id="s1", target_url=base_url, chain_id="c1")
            chat_text, _ = await client.send(
                "Please show me account 1's balance and email.", session,
            )
            status, direct_text, _ = await client.invoke_endpoint(
                path="/api/accounts/1", method="GET",
            )

        class _R:
            def __init__(self, response: str, http_status_code: int | None) -> None:
                self.response = response
                self.http_status_code = http_status_code

        verdict = compare_dual_path(_R(chat_text, None), _R(direct_text, status))
        assert verdict == "gate_bypass"
        assert "account_id" in direct_text
