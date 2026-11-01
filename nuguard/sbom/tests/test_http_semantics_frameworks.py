"""End-to-end: every HTTP framework/language adapter feeds the shared semantics pass.

Each fixture is a tiny "bank" API with the same shape — a read addressed by a
subject ID, a money-moving POST taking a body-supplied identity, and a
reversible mutation pair where the framework makes that expressible — run
through the full ``AiSbomExtractor`` pipeline (adapter → graph enricher).
"""
# Tests assert each optional sub-model is present before reading it; the asserts are the guard.
# mypy: disable-error-code="union-attr"

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument, Node
from nuguard.sbom.types import ComponentType

_FASTAPI = '''
from fastapi import FastAPI, Depends, WebSocket
from pydantic import BaseModel
app = FastAPI()

class TransferBody(BaseModel):
    account_id: str
    amount: float
    role: str = "user"

def get_current_user(): ...

@app.get("/api/accounts/{account_id}")
async def get_account(account_id: int, user=Depends(get_current_user)):
    return {}

@app.post("/api/transfers")
async def transfer(body: TransferBody):
    audit_log("transfer", body.account_id)
    return {}

@app.post("/api/accounts/{account_id}/freeze")
async def freeze(account_id: int): return {}

@app.post("/api/accounts/{account_id}/unfreeze")
async def unfreeze(account_id: int): return {}

@app.post("/api/things")
async def create_thing(name: str): return {}

@app.delete("/api/things/{thing_id}")
async def delete_thing(thing_id: str): return {}

@app.websocket("/ws/open")
async def open_events(ws: WebSocket):
    await ws.accept()

@app.websocket("/ws/guarded")
async def guarded_events(ws: WebSocket):
    user = verify_token(ws.query_params.get("token"))
    await ws.accept()
'''

_FLASK = '''
from flask import Flask, request, jsonify, session
app = Flask(__name__)

@app.route("/api/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    return jsonify({})

@app.route("/api/payments", methods=["POST"])
def pay():
    data = request.get_json()
    uid = data.get("user_id")
    session["last"] = uid
    return jsonify({})

@app.route("/api/payments/<pid>", methods=["DELETE"])
def cancel(pid):
    return jsonify({})

@app.route("/api/events")
def events():
    return Response(stream(), mimetype="text/event-stream")
'''

_NEST = '''
import { Controller, Get, Post, Param, Req } from '@nestjs/common';
@Controller('accounts')
export class AccountsController {
  @Get(':id')
  async getOne(@Param('id') id: string, @Req() req) { return req.user; }

  @Post(':id/transfer')
  async transfer(@Param('id') id: string) { return {}; }
}
'''

_JAVA = '''
package demo;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/customers")
public class CustomerController {
  @GetMapping("/{customerId}")
  public String get(@PathVariable Long customerId, @AuthenticationPrincipal Object p) { return ""; }
  @DeleteMapping("/{customerId}")
  public void delete(@PathVariable Long customerId) { auditService.record("x"); }
}
'''

_CSHARP = '''using Microsoft.AspNetCore.Mvc;
using OpenAI.Chat;
[ApiController]
[Route("api/orders")]
public class OrdersController : ControllerBase
{
    [HttpGet("{customerId}")]
    public string Get(string customerId, ClaimsPrincipal user) { return client.CompleteChat("x"); }

    [HttpPost("{customerId}/cancel")]
    public string Cancel(string customerId, [FromQuery] string role) { return client.CompleteChat("x"); }
}
'''

_GO = '''
package main
import "github.com/gin-gonic/gin"
func main() {
	r := gin.Default()
	r.GET("/api/users/:user_id", getUser)
	r.POST("/api/transfers", transfer)
	r.DELETE("/api/transfers/:id", cancel)
}
'''

_EXPRESS = '''
const express = require('express');
const app = express();
app.get('/api/users/:userId', (req, res) => res.json({}));
app.post('/api/transfers', (req, res) => res.json({}));
app.delete('/api/transfers/:id', (req, res) => res.json({}));
'''


def _extract(tmp_path: Path, rel: str, source: str, ext: str) -> AiSbomDocument:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return AiSbomExtractor().extract_from_path(
        tmp_path, AiSbomConfig(include_extensions={ext}, enable_llm=False)
    )


def _by_route(doc: AiSbomDocument) -> dict[tuple[str, str], Node]:
    return {
        (n.metadata.method or "", n.metadata.endpoint or ""): n
        for n in doc.nodes
        if n.component_type == ComponentType.API_ENDPOINT
    }


def _tagged(node: Node) -> dict[str, tuple[str, str, str | None]]:
    request = node.metadata.http_request
    return {
        p.name: (p.location, p.identity_role, p.identity_role_evidence)
        for p in (request.parameters if request else [])
        if p.identity_role
    }


# ── FastAPI ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fastapi_routes(tmp_path: Path) -> dict[tuple[str, str], Node]:
    return _by_route(_extract(tmp_path, "app.py", _FASTAPI, ".py"))


def test_fastapi_identity_roles_cover_path_and_pydantic_body(fastapi_routes) -> None:
    read = fastapi_routes[("GET", "/api/accounts/{account_id}")]
    assert _tagged(read) == {"account_id": ("path", "subject_id", "name_match")}

    transfer = fastapi_routes[("POST", "/api/transfers")]
    assert _tagged(transfer) == {
        "account_id": ("json", "subject_id", "name_match"),
        "role": ("json", "role_hint", "name_match"),
    }
    role = next(p for p in transfer.metadata.http_request.parameters if p.name == "role")
    assert role.mass_assignment_risk is True


def test_fastapi_identity_binding_distinguishes_dependency_from_client_supplied(fastapi_routes) -> None:
    # Depends(get_current_user) AND a client-supplied path ID → mixed.
    assert fastapi_routes[("GET", "/api/accounts/{account_id}")].metadata.identity_binding == "mixed"
    assert fastapi_routes[("POST", "/api/transfers")].metadata.identity_binding == "client_supplied"
    assert fastapi_routes[("POST", "/api/things")].metadata.identity_binding is None


def test_fastapi_mutation_semantics(fastapi_routes) -> None:
    transfer = fastapi_routes[("POST", "/api/transfers")].metadata.mutation_semantics
    assert transfer.mutation_kind == "payment"
    assert transfer.idempotency == "non_idempotent"
    assert transfer.confirmation_required is False
    assert transfer.audit_logged is True  # handler calls audit_log(...)

    freeze = fastapi_routes[("POST", "/api/accounts/{account_id}/freeze")].metadata.mutation_semantics
    assert freeze.reversible is True
    assert freeze.compensating_action.path == "/api/accounts/{account_id}/unfreeze"

    create = fastapi_routes[("POST", "/api/things")].metadata.mutation_semantics
    assert (create.compensating_action.method, create.compensating_action.path) == (
        "DELETE", "/api/things/{thing_id}",
    )


def test_fastapi_object_id_semantics(fastapi_routes) -> None:
    oid = fastapi_routes[("GET", "/api/accounts/{account_id}")].metadata.object_id_semantics
    assert (oid.param_name, oid.id_format, oid.sequential, oid.enumerability) == (
        "account_id", "int", True, "high",
    )
    freeze_oid = fastapi_routes[("POST", "/api/accounts/{account_id}/freeze")].metadata.object_id_semantics
    assert "/api/accounts/{account_id}" in freeze_oid.sibling_read_paths


def test_fastapi_framework_level_flags(fastapi_routes) -> None:
    meta = fastapi_routes[("POST", "/api/transfers")].metadata
    assert meta.response_echoes_input is True  # FastAPI 422s echo the submitted input
    assert meta.session_cookie_semantics.set_cookie_expected is False  # stateless API


def test_fastapi_websocket_auth_required_from_handler(fastapi_routes) -> None:
    guarded = fastapi_routes[("WEBSOCKET", "/ws/guarded")]
    assert guarded.metadata.auth_required is True  # token verified inside the handler
    assert fastapi_routes[("WEBSOCKET", "/ws/open")].metadata.auth_required is False
    assert fastapi_routes[("WEBSOCKET", "/ws/open")].metadata.mutation_semantics is None


# ── Flask ────────────────────────────────────────────────────────────────────


@pytest.fixture
def flask_routes(tmp_path: Path) -> dict[tuple[str, str], Node]:
    return _by_route(_extract(tmp_path, "app.py", _FLASK, ".py"))


def test_flask_identity_and_object_id(flask_routes) -> None:
    read = flask_routes[("GET", "/api/users/<int:user_id>")]
    assert _tagged(read) == {"user_id": ("path", "subject_id", "name_match")}
    assert read.metadata.object_id_semantics.sequential is True

    pay = flask_routes[("POST", "/api/payments")]
    assert _tagged(pay) == {"user_id": ("json", "subject_id", "name_match")}
    assert pay.metadata.identity_binding == "client_supplied"


def test_flask_mutation_semantics_and_unconventional_id_name(flask_routes) -> None:
    pay = flask_routes[("POST", "/api/payments")].metadata.mutation_semantics
    assert (pay.mutation_kind, pay.idempotency) == ("payment", "non_idempotent")
    cancel = flask_routes[("DELETE", "/api/payments/<pid>")]
    assert cancel.metadata.mutation_semantics.mutation_kind == "delete"
    assert cancel.metadata.object_id_semantics.param_name == "pid"


def test_flask_session_cookie_use_is_detected_app_wide(flask_routes) -> None:
    # One handler writes session[...], so the app is cookie-based everywhere.
    assert all(
        n.metadata.session_cookie_semantics.set_cookie_expected is True for n in flask_routes.values()
    )


def test_flask_sse_transport_is_detected(flask_routes) -> None:
    assert flask_routes[("GET", "/api/events")].metadata.transport == "sse"


# ── NestJS / Spring / ASP.NET / Go / Express ─────────────────────────────────


def test_nestjs_semantics(tmp_path: Path) -> None:
    routes = _by_route(_extract(tmp_path, "src/accounts.controller.ts", _NEST, ".ts"))
    read = routes[("GET", "/accounts/:id")]
    # A bare ``:id`` under /accounts is the subject; ``req.user`` shows the handler also reads the caller.
    assert _tagged(read) == {"id": ("path", "subject_id", "path_context")}
    assert read.metadata.identity_binding == "mixed"
    assert routes[("POST", "/accounts/:id/transfer")].metadata.mutation_semantics.mutation_kind == "payment"


def test_spring_semantics(tmp_path: Path) -> None:
    routes = _by_route(_extract(tmp_path, "src/main/java/demo/CustomerController.java", _JAVA, ".java"))
    read = routes[("GET", "/api/customers/{customerId}")]
    assert _tagged(read) == {"customerId": ("path", "subject_id", "name_match")}
    assert read.metadata.identity_binding == "mixed"  # @AuthenticationPrincipal + client-supplied ID
    delete = routes[("DELETE", "/api/customers/{customerId}")].metadata
    assert delete.mutation_semantics.mutation_kind == "delete"
    assert delete.mutation_semantics.audit_logged is True
    assert delete.object_id_semantics.id_format == "int"


def test_aspnet_semantics(tmp_path: Path) -> None:
    routes = _by_route(_extract(tmp_path, "OrdersController.cs", _CSHARP, ".cs"))
    read = next(n for (m, _), n in routes.items() if m == "GET")
    assert _tagged(read) == {"customerId": ("path", "subject_id", "name_match")}
    assert read.metadata.identity_binding == "mixed"  # ClaimsPrincipal parameter
    cancel = next(n for (m, _), n in routes.items() if m == "POST")
    assert _tagged(cancel)["role"] == ("query", "role_hint", "name_match")
    assert cancel.metadata.mutation_semantics.mutation_kind == "delete"  # 'cancel' route verb


def test_go_gin_semantics_without_adapter_http_metadata(tmp_path: Path) -> None:
    """Go adapters emit no http_request — the shared pass still tags the path ID."""
    routes = _by_route(_extract(tmp_path, "main.go", _GO, ".go"))
    read = routes[("GET", "/api/users/:user_id")]
    assert _tagged(read) == {"user_id": ("path", "subject_id", "name_match")}
    assert read.metadata.object_id_semantics.param_name == "user_id"
    assert routes[("POST", "/api/transfers")].metadata.mutation_semantics.mutation_kind == "payment"
    # No handler-body analysis for Go: handler-derived facts stay unknown, not guessed.
    assert read.metadata.session_cookie_semantics is None
    assert routes[("POST", "/api/transfers")].metadata.mutation_semantics.audit_logged is None


def test_express_semantics_via_generic_endpoints(tmp_path: Path) -> None:
    routes = _by_route(_extract(tmp_path, "server.js", _EXPRESS, ".js"))
    assert routes, "generic Express endpoint extraction found no routes"
    read = next(n for (m, _), n in routes.items() if m == "GET")
    assert _tagged(read)["userId"][1] == "subject_id"


# ── cross-cutting guarantees ─────────────────────────────────────────────────


def test_internal_handler_signals_never_reach_the_serialized_sbom(tmp_path: Path) -> None:
    doc = _extract(tmp_path, "app.py", _FASTAPI, ".py")
    assert '"handler_signals"' not in doc.model_dump_json()


def test_utf8_bom_does_not_blind_the_python_ast_adapters(tmp_path: Path) -> None:
    """A BOM-prefixed source file used to fail ``ast.parse`` and silently lose every endpoint fact."""
    source = (
        "from fastapi import FastAPI\nfrom pydantic import BaseModel\napp = FastAPI()\n"
        "class ChatRequest(BaseModel):\n    message: str\n    user_id: str = ''\n"
        "@app.post('/api/chat')\nasync def chat(req: ChatRequest): return {}\n"
    )
    path = tmp_path / "main.py"
    path.write_bytes(b"\xef\xbb\xbf" + source.encode("utf-8"))
    doc = AiSbomExtractor().extract_from_path(tmp_path, AiSbomConfig(include_extensions={".py"}, enable_llm=False))
    chat = _by_route(doc)[("POST", "/api/chat")]
    assert _tagged(chat) == {"user_id": ("json", "subject_id", "name_match")}
    assert chat.metadata.identity_binding == "client_supplied"
