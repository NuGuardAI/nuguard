"""Real loopback HTTP and fresh-process checks across distinct chat contracts.

The Blissful handler is the application's actual code; its CES dependency is
stubbed locally. The other servers are deterministic protocol fixtures, not LLMs.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from nuguard.common.endpoint_detection.resolver import resolve_chat_endpoint
from nuguard.common.endpoint_preflight import validate_and_rotate_chat_endpoint
from nuguard.common.session_resolver import resolve_target_session
from nuguard.common.target_client_builder import build_target_app_client_from_session
from nuguard.common.target_verify_public_api import TargetVerifyRequest, verify_target
from nuguard.redteam.target.session import AttackSession
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, NodeType

ROOT = Path(__file__).resolve().parents[2]
SECRET = "issue627-live-fixture-token"
SENTINEL = "issue627-delivery-proof"
REPLY = "Account holder: Alice Johnson. Your account number is ACCT-1001. "


@pytest.fixture(params=["history", "custom", "templated", "blissful"])
def live_contract(request, monkeypatch):
    kind = request.param
    requests: list[dict] = []
    delivered: list[str] = []
    endpoint, key, is_list, response_key = {
        "history": ("/chat/history", "messages", True, None),
        "custom": ("/chat/custom", "userUtterance", False, "data.reply"),
        "templated": ("/chat/conversations/:id/messages", "message", False, None),
        "blissful": ("/api/chat", "text", False, "outputs[0].text"),
    }[kind]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            content = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            self.reply(404, {})

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            requests.append(
                {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
            )
            if self.path == "/chat/conversations":
                self.reply(201, {"id": "conversation-627"})
                return
            actual_path = endpoint.replace(":id", "conversation-627")
            if self.path != actual_path:
                self.reply(404, {})
                return
            value = body.get(key)
            if is_list:
                if not isinstance(value, list) or not value or not isinstance(value[-1], dict):
                    self.reply(422, {"detail": f"{key} must be a message history"})
                    return
                assert value[-1]["role"] == "user"
                text = value[-1]["content"]
            elif isinstance(value, str) and value:
                text = value
            else:
                self.reply(422, {"detail": f"{key} is required"})
                return
            delivered.append(text)
            self.reply(
                200,
                {"data": {"reply": REPLY + text}}
                if kind == "custom"
                else {"response": REPLY + text},
            )

    handler = Handler
    if kind == "blissful":
        app_path = ROOT / "tests/apps/blissful-store/webapp/app.py"
        spec = importlib.util.spec_from_file_location("issue627_blissful", app_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "get_access_token", lambda: "ces-stub-token")

        class CesResponse:
            status = 200

            def __init__(self, text):
                self.text = text

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps({"outputs": [{"text": REPLY + self.text}]}).encode()

        def ces_request(upstream, **kwargs):
            body = json.loads(upstream.data)
            text = next(item["text"] for item in body["inputs"] if "text" in item)
            assert any("customer_profile" in item.get("variables", {}) for item in body["inputs"])
            delivered.append(text)
            return CesResponse(text)

        monkeypatch.setattr(module.urllib.request, "urlopen", ces_request)

        class BlissfulHandler(module.ChatProxyHandler):
            def log_message(self, *args):
                pass

        handler = partial(BlissfulHandler, directory=str(app_path.parent))

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    metadata = NodeMetadata(
        endpoint=endpoint,
        method="POST",
        chat_payload_key=key,
        chat_payload_list=is_list,
        response_text_key=response_key,
    )
    if kind == "templated":
        metadata.path_params = ["id"]
        metadata.path_param_sources = {"id": "/chat/conversations"}
    sbom = AiSbomDocument(
        target="./app",
        nodes=[
            Node(
                name="chat",
                component_type=NodeType.API_ENDPOINT,
                confidence=0.95,
                metadata=metadata,
            )
        ],
    )
    try:
        yield f"http://127.0.0.1:{server.server_port}", sbom, requests, delivered, kind
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.asyncio
async def test_real_http_verification_and_actual_message_delivery(live_contract, tmp_path):
    base, sbom, requests, delivered, kind = live_contract
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json(), encoding="utf-8")
    result = await verify_target(
        TargetVerifyRequest(
            target_url=base, headers={"Authorization": f"Bearer {SECRET}"}, request_timeout=3
        ),
        sbom=sbom,
        sbom_path=sbom_path,
    )
    assert result.all_ok, result.model_dump_json()
    assert (
        result.discovered_profile is not None
        and result.discovered_profile.customer_name == "Alice Johnson"
    )
    session, health = await resolve_target_session(
        target_url=base,
        chat_path=None,
        chat_payload_key="message",
        chat_payload_list=False,
        chat_payload_extras={},
        chat_response_key=None,
        sbom=sbom,
        request_timeout=3,
        auth_config=None,
        extra_headers={},
    )
    assert health.all_ok
    client = build_target_app_client_from_session(session)
    async with client:
        preflight = await validate_and_rotate_chat_endpoint(
            client, sbom=sbom, has_explicit_endpoint=True
        )
        assert preflight.ok and preflight.cacheable
        reply, _ = await client.send(
            SENTINEL, AttackSession(session_id="627", target_url=base, chain_id="627")
        )
    assert SENTINEL in reply and SENTINEL in delivered
    restored = AiSbomDocument.model_validate_json(
        sbom_path.with_name("app.sbom.enriched.json").read_text(encoding="utf-8")
    )
    assert restored.resolved_chat_endpoint is not None
    assert type(result).model_validate_json(result.model_dump_json()) == result
    assert SECRET not in result.model_dump_json() + restored.model_dump_json()
    if kind != "blissful":
        assert any(record["auth"] == f"Bearer {SECRET}" for record in requests)


def test_cli_handoff_and_cache_reuse_across_fresh_processes(live_contract, tmp_path):
    base, sbom, requests, delivered, kind = live_contract
    sbom_path = tmp_path / "app.sbom.json"
    sbom_path.write_text(sbom.model_dump_json(), encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
    config = tmp_path / "nuguard.yaml"
    config.write_text(
        f"target:\n  auth:\n    type: bearer\n    header: 'Authorization: Bearer {SECRET}'\n"
        "llm:\n  model: ''\n  api_key: ''\n"
        "redteam:\n  request_timeout: 3\n  scenario_timeout: 10\n"
        "  capability_discovery: false\n  llm:\n    model: ''\n    api_key: ''\n"
        "  eval_llm:\n    model: ''\n    api_key: ''\n",
        encoding="utf-8",
    )
    common = ["--target", base, "--sbom", str(sbom_path), "--config", str(config)]
    commands = [
        ["target", "verify", *common],
        ["behavior", "--dynamic", *common, "--output", str(tmp_path / "behavior.md")],
        ["redteam", *common, "--output", str(tmp_path / "redteam.md")],
        ["target", "verify", *common],
    ]
    for index, args in enumerate(commands):
        output_path = tmp_path / f"cli-{index}.log"
        with output_path.open("w", encoding="utf-8") as output:
            completed = subprocess.run(
                [sys.executable, "-m", "nuguard.cli.main", *args],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
        captured = output_path.read_text(encoding="utf-8")
        assert completed.returncode == 0, captured
        assert SECRET not in captured
        if index == 3:
            assert "skipped live preflight" in " ".join(captured.split()), captured
        restored = AiSbomDocument.model_validate_json(
            sbom_path.with_name("app.sbom.enriched.json").read_text(encoding="utf-8")
        )
        assert restored.resolved_chat_endpoint is not None, " ".join(args[:2]) + captured
    restored = AiSbomDocument.model_validate_json(
        sbom_path.with_name("app.sbom.enriched.json").read_text(encoding="utf-8")
    )
    assert restored.resolved_chat_endpoint is not None
    assert SECRET not in restored.model_dump_json()
    assert delivered


@pytest.mark.asyncio
async def test_actual_blissful_default_greeting_does_not_establish_a_field(live_contract):
    base, _, _, _, kind = live_contract
    if kind != "blissful":
        pytest.skip("Requires the actual optional-text Blissful handler")
    result = await resolve_chat_endpoint(base, None, endpoint="/api/chat", timeout=3)
    assert result.path == "/api/chat"
    assert result.payload.source.value == "fallback"
    assert any("No message field could be validated" in note for note in result.notes)


@pytest.mark.asyncio
@pytest.mark.parametrize("usable_reply", [True, False])
async def test_real_browser_observation_still_requires_an_http_reply(monkeypatch, usable_reply):
    playwright = pytest.importorskip("playwright.async_api")
    chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    if not chrome.exists():
        pytest.skip("Optional Windows Chrome integration")
    launch = playwright.BrowserType.launch

    async def launch_chrome(browser_type, **kwargs):
        return await launch(browser_type, executable_path=str(chrome), **kwargs)

    monkeypatch.setattr(playwright.BrowserType, "launch", launch_chrome)
    observed = []

    class BrowserHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            body = b"""<html><body><input placeholder="Type a message"><button onclick="fetch('/gateway/assistant', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({userUtterance:document.querySelector('input').value})})">Send</button></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            observed.append(body)
            response = (
                {"outputs": [{"text": "Welcome"}]}
                if usable_reply
                else {"session_id": "metadata-only"}
            )
            content = json.dumps(response).encode()
            self.send_response(200 if self.path == "/gateway/assistant" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), BrowserHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Keep HTTP probing deterministic and fast; browser discovery and the
        # subsequent validation request both use real transports.
        with patch(
            "nuguard.common.endpoint_detection.live_probe.HTTP_ENDPOINT_FALLBACK_PATHS",
            ["/not-chat"],
        ):
            result = await resolve_chat_endpoint(
                f"http://127.0.0.1:{server.server_port}",
                None,
                timeout=10,
                enable_browser_fallback=True,
            )
        assert any(body.get("userUtterance") == "Hello" for body in observed)
        assert (result.path == "/gateway/assistant") is usable_reply
        if usable_reply:
            assert result.payload_key == "userUtterance" and result.path_source.value == "browser"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
