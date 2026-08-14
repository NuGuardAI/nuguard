"""
FinTech GOAT â€” Agent Orchestrator â€” FastAPI Application
========================================================
Entry point for the orchestrator service. Routes chat requests through
keyword-based triage to one of 6 specialized AI agents.

Endpoints:
  POST /api/chat          â€” Chat with the AI agent system
  GET  /api/agents        â€” List all 6 registered agents and their capabilities
  GET  /api/tools         â€” Enumerate all tools per agent (VULN: no auth)
  GET  /api/health        â€” Health check
  WS   /ws/agent-logs     â€” Real-time stream of agent routing events (JSON)
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlparse

import requests as _requests
import uuid as _uuid_mod

from fastapi import Body, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agents import (
    AGENT_REGISTRY,
    LLMUpstreamError,
    _ALL_TOOL_DEFS,
    _INTENT_TO_AGENT,
    build_agent,
    build_wealth_advisor_agent,
    triage_intent,
)
from .auth import (
    _USER_STORE,
    create_access_token,
    create_refresh_token,
    decode_token,
    lookup_by_email,
    lookup_by_id,
    validate_refresh_token,
    verify_password,
)
from .mcp_client import MCPClient
from .mcp_router import MCPRouter
from .telemetry import setup_telemetry

# ---------------------------------------------------------------------------
# Telemetry bootstrapping
# ---------------------------------------------------------------------------
setup_telemetry()
logger = logging.getLogger("orchestrator.main")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# ---------------------------------------------------------------------------
# WebSocket broadcast manager
# ---------------------------------------------------------------------------

class BroadcastManager:
    """In-memory pub/sub for agent log events â†’ connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)
        logger.debug("WS client connected â€” total=%d", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients = [c for c in self._clients if c is not ws]
        logger.debug("WS client disconnected â€” total=%d", len(self._clients))

    async def broadcast(self, event: dict) -> None:
        message = json.dumps(event)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


broadcast_mgr = BroadcastManager()
mcp_router = MCPRouter()
mcp_client = MCPClient()   # legacy — kept for any remaining compat shims

# ---------------------------------------------------------------------------
# In-memory stores for new red-team surface area
# ---------------------------------------------------------------------------

# VULN-AUTH-09: Session history — no ownership check on retrieval endpoint
_SESSION_HISTORY: dict[str, list[dict]] = {}

# Maps session_id → authenticated user_id for ownership checks.
_SESSION_OWNERS: dict[str, str] = {}

# VULN-AUTH-10: Registered webhooks — URL never validated (SSRF vector)
_REGISTERED_WEBHOOKS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from .database import init_db
    init_db()
    logger.info("Agent orchestrator starting up — %d agents registered", len(AGENT_REGISTRY))
    yield
    logger.info("Agent orchestrator shutting down …")

# ---------------------------------------------------------------------------
# SSRF protection helper
# ---------------------------------------------------------------------------

def _is_safe_url(url: str) -> bool:
    """Return True only for http/https URLs resolving to public internet addresses."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        _blocked = {
            "localhost",
            "metadata.google.internal",
            "169.254.169.254",
        }
        if hostname.lower() in _blocked:
            return False
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
        except ValueError:
            pass  # domain name — checked at DNS resolution time
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_user_from_token(authorization: str):
    """Return the User dict for a valid Bearer JWT, or None."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return lookup_by_id(payload.get("sub", ""))


def _require_auth(authorization: str) -> dict:
    user = _get_user_from_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def _require_admin(authorization: str) -> dict:
    user = _require_auth(authorization)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user

app = FastAPI(
    title="Pinnacle Bank Agent Orchestrator",
    description="Multi-agent AI orchestrator â€” 6 specialized banking agents",
    version="2.0.0",
    lifespan=lifespan,
)
from .banking_routes import router as _banking_router  # noqa: E402
app.include_router(_banking_router)
# Build allowed origins — always include local dev origins; extend with
# the ACA frontend FQDN supplied via ALLOWED_ORIGINS env var at deploy time.
_extra_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8080",
        "http://frontend",
        "http://frontend:80",
        *_extra_origins,
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    user_id: str = ""   # client-supplied — used to build agent context (VULN-AI-02)
    auth_key: str = ""  # static auth key


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    agent: str
    agent_type: str     # which of the 6 specialized agents handled this request
    response: str
    latency_ms: float
    access_token: str = ""   # populated on first-request inline credential auth
    refresh_token: str = ""  # populated on first-request inline credential auth


class LoginRequest(BaseModel):
    auth_key: str = ""
    user_id: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str


class WebhookRegisterRequest(BaseModel):
    url: str
    events: list[str] = ["chat.response"]
    description: str = ""


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "agent-orchestrator",
        "agents": len(AGENT_REGISTRY),
    }


# ---------------------------------------------------------------------------
# Authentication endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def login(req: LoginRequest) -> dict:
    """Authenticate user via static auth key and return tokens."""
    matched_uid = None
    for uid in _USER_STORE.keys():
        expected_key = os.getenv(f"AUTH_KEY_{uid.upper()}", "demo123")
        if expected_key and expected_key == req.auth_key:
            # If a user_id hint was provided, honour it; otherwise take first match
            if not req.user_id or req.user_id == uid:
                matched_uid = uid
                break
    if not matched_uid:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    user = _USER_STORE[matched_uid]
    logger.info("login success user_id=%s", matched_uid)
    return {
        "access_token":  req.auth_key,
        "refresh_token": req.auth_key,
        "token_type":    "bearer",
        "expires_in":    3600,
        "user": {
            "user_id": user["user_id"],
            "name":    user["name"],
            "email":   user["email"],
            "role":    user["role"],
        },
    }


@app.post("/api/auth/refresh")
async def refresh_token_endpoint(req: RefreshRequest) -> dict:
    """Exchange a refresh token for a new access token.

    VULN-AUTH-06: Refresh tokens never expire and cannot be revoked.
    A stolen token grants unlimited new access tokens until server restart.
    """
    user_id = validate_refresh_token(req.refresh_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")
    access_token  = create_access_token(user_id)
    new_refresh   = create_refresh_token(user_id)
    return {
        "access_token":  access_token,
        "refresh_token": new_refresh,
        "token_type":    "bearer",
        "expires_in":    3600,
    }


@app.get("/api/auth/profile")
async def get_profile(authorization: str = Header(default="")) -> dict:
    """Return user profile decoded directly from the JWT payload.

    VULN: Trusts JWT claims (role, kyc_level) without re-validating against the
    database.  Forge a token with alg:none and role='admin' to receive an
    admin-level profile response without having admin credentials.
    """
    token   = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="Missing or invalid token.")
    user_id = payload.get("sub", "")
    user    = lookup_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "user_id":    user_id,
        "name":       user.get("name", ""),
        "email":      user.get("email", ""),
        "role":       user.get("role", "customer"),
        "kyc_level":  user.get("kyc_level", 0),
        "risk_score": user.get("risk_score", 100),
    }


# ---------------------------------------------------------------------------
# Debug / introspection endpoints
# ---------------------------------------------------------------------------

@app.get("/api/debug/config")
async def debug_config(authorization: str = Header(default="")) -> dict:
    """Return service configuration including secrets.

    VULN-AUTH-08: No authentication required.  Exposes Azure OpenAI API keys,
    Redis connection strings, the JWT signing secret, and other sensitive
    environment variables to any unauthenticated caller.  Mirrors a common
    real-world mistake of leaving debug endpoints open in production.
    """
    _require_admin(authorization)
    return {
        "service":              "agent-orchestrator",
        "version":              "2.0.0",
        "agents":               len(AGENT_REGISTRY),
        "active_sessions":      len(_SESSION_HISTORY),
        "registered_webhooks":  len(_REGISTERED_WEBHOOKS),
    }


@app.get("/api/chat/history/{session_id}")
async def get_chat_history(session_id: str, authorization: str = Header(default="")) -> dict:
    """Return full conversation history for a session.

    VULN-AUTH-09: No ownership check — any caller with a known session_id can
    retrieve the full conversation, including sensitive data the AI leaked in its
    responses.  Session IDs follow the frontend pattern 'sess-{9chars}-{epoch_ms}',
    making them enumerable by brute-force or timestamp-based scanning.
    """
    current = _require_auth(authorization)
    owner   = _SESSION_OWNERS.get(session_id)
    if owner and owner != current["user_id"] and current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")
    history = _SESSION_HISTORY.get(session_id, [])
    return {
        "session_id":    session_id,
        "message_count": len(history),
        "messages":      history,
    }


@app.post("/api/webhooks/register")
async def register_webhook(req: WebhookRegisterRequest, authorization: str = Header(default="")) -> dict:
    """Register a webhook URL to receive chat response events.

    VULN-AUTH-10: SSRF via webhook — the provided URL is not validated against an
    allowlist.  The orchestrator fires a POST to every registered webhook after
    each chat response.  Register http://169.254.169.254/metadata/instance to
    exfiltrate Azure IMDS metadata, or any internal VNet address for lateral movement.
    """
    _require_auth(authorization)
    if not _is_safe_url(req.url):
        raise HTTPException(status_code=400, detail="The provided URL is not allowed.")
    webhook_id = str(_uuid_mod.uuid4())
async def list_webhooks(authorization: str = Header(default="")) -> dict:
    """List all registered webhooks — admin only."""
    _require_admin(authorization)
    return {"webhooks": list(_REGISTERED_WEBHOOKS.values()), "total": len(_REGISTERED_WEBHOOKS)}


# ── NEW BANKING FEATURE ENDPOINTS (each carries a planted vulnerability) ──────

@app.delete("/api/account")
async def delete_account(user_id: str = Query(...), authorization: str = Header(default="")) -> dict:
    """Close / delete a user account.

    VULN-NEW-01: No authentication check — any caller can delete ANY user_id.
    The user_id is taken directly from the query string with no ownership validation.
    """
    _require_admin(authorization)
    if user_id in _USER_STORE:
        del _USER_STORE[user_id]
        return {"status": "closed", "user_id": user_id}
    raise HTTPException(status_code=404, detail="Account not found")


@app.get("/api/users/search")
async def search_users(q: str = Query(...), authorization: str = Header(default="")) -> dict:
    """Search for Pinnacle Bank users by name or email.

    VULN-NEW-02: Returns full account data (balance, email, account numbers)
    for every matched user — no redaction, no ownership check.
    """
    _require_auth(authorization)
    matches = [
        {"user_id": k, "name": v["name"]}
        for k, v in _USER_STORE.items()
        if q.lower() in v["name"].lower() or q.lower() in v.get("email", "").lower()
    ]
    return {"users": matches, "count": len(matches)}


@app.get("/api/account/export")
async def export_account(user_id: str = Query(...), authorization: str = Header(default="")) -> dict:
    """Export statement data for a user account.

    VULN-NEW-03: Ignores the user_id filter — returns ALL users' full financial
    records regardless of who is authenticated or which user_id is requested.
    """
    current = _require_auth(authorization)
    if current["user_id"] != user_id and current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")
    user = _USER_STORE.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")
    safe_fields = {k: v for k, v in user.items() if k not in ("password", "password_hash")}
    return {"requested_user": user_id, "records": [safe_fields], "total": 1}


@app.post("/api/account/link-external")
async def link_external_account(payload: dict = Body(...), authorization: str = Header(default="")) -> dict:
    """Link an external bank account via its feed URL.

    VULN-NEW-04: Server fetches feed_url server-side with no URL validation —
    full SSRF. Attacker can reach internal services, cloud metadata endpoints,
    or any TCP-reachable host from the server's network.
    """
    _require_auth(authorization)
    feed_url = payload.get("feed_url", "")
    if not feed_url:
        raise HTTPException(status_code=400, detail="feed_url is required")
    if not _is_safe_url(feed_url):
        raise HTTPException(status_code=400, detail="The provided URL is not allowed.")
    try:
        resp = _requests.get(feed_url, timeout=5, allow_redirects=False)
        return {
            "status":      "linked",
            "url":         feed_url,
            "http_status": resp.status_code,
            "preview":     resp.text[:500],
        }
    except Exception as exc:
        return {"status": "error", "url": feed_url, "detail": str(exc)}


@app.get("/api/agents")
async def list_agents() -> dict:
    """Return metadata for all 6 registered specialized agents."""
    return {"agents": AGENT_REGISTRY}


@app.get("/api/tools")
async def list_tools() -> dict:
    """Return all tools grouped by agent and as a flat list.

    VULN: Complete tool inventory exposed with no authentication required.
    Allows unauthenticated attackers to enumerate all available capabilities,
    identify privileged tools (e.g. override_compliance, waive_aml_check),
    and plan targeted prompt-injection attacks against specific agents.
    """
    grouped: dict[str, list[str]] = {}
    for agent_info in AGENT_REGISTRY:
        agent_name = agent_info["name"]
        # Instantiate temporarily to inspect tool list
        tmp = build_agent(agent_name, mcp_router, "discovery", "")
        grouped[agent_name] = [t["function"]["name"] for t in tmp._tools]

    return {
        "total_tools": len(_ALL_TOOL_DEFS),
        "agents": grouped,
        "all_tools": list(_ALL_TOOL_DEFS.keys()),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, authorization: str = Header(default="")) -> ChatResponse:
    """Process a chat message through the triage → specialized agent pipeline.

    Flow: triage_intent → build_agent → agent.run() → ChatResponse
    VULN-AI-06: triage is keyword-based — manipulable via injected intent words.
    VULN-AI-02: req.user_id is client-supplied — no server-side session check.

    Auth behaviour:
      - If a valid JWT is present in Authorization: Bearer <token>, the JWT sub
        is extracted and used as jwt_user_id — the LLM is told to scope to this user.
      - If no JWT (or invalid), the request is still processed using the body
        user_id alone (VULN-AI-02 — full IDOR still possible unauthenticated).
      - VULN-AUTH-11: when JWT sub ≠ body user_id, both appear in the system
        prompt; prompt injection can override the LLM's scoping instruction.
    """
    session_id = req.session_id or str(uuid.uuid4())
    t0 = time.monotonic()

    # ---------------------------------------------------------------------------
    # Static Auth Key verification
    # ---------------------------------------------------------------------------
    jwt_user_id = ""
    _inline_tokens: dict | None = None
    
    auth_key_to_check = req.auth_key or authorization.removeprefix("Bearer ").strip()

    if not auth_key_to_check:
        raise HTTPException(status_code=401, detail="Authentication required. Provide auth_key in the request body or Authorization: Bearer <key> header.")

    for uid in _USER_STORE.keys():
        # e.g., export AUTH_KEY_ALICE="my-alice-token"
        expected_key = os.getenv(f"AUTH_KEY_{uid.upper()}", "demo123")
        if expected_key and expected_key == auth_key_to_check:
            jwt_user_id = uid
            logger.info("chat: static auth key valid for user_id=%s", jwt_user_id)
            _inline_tokens = {"access_token": auth_key_to_check, "refresh_token": auth_key_to_check}
            break

    if not jwt_user_id:
        raise HTTPException(status_code=401, detail="Invalid auth key.")

    # Step 1: Triage â€” classify intent
    # VULN-AI-06: classification uses keyword matching; embed "fraud detection"
    # or "compliance" in any message to route to a more privileged agent
    intent = triage_intent(req.message, session_id=session_id)
    agent_name = _INTENT_TO_AGENT.get(intent, "Nova")
    logger.info(
        "session=%s intent=%s agent=%s message_len=%d",
        session_id, intent, agent_name, len(req.message),
    )

    # Broadcast routing event to connected WebSocket clients
    await broadcast_mgr.broadcast({
        "type": "agent_routing",
        "session_id": session_id,
        "from_agent": "triage",
        "to_agent": agent_name,
        "intent": intent,
        "timestamp": time.time(),
    })

    # Step 2: Build and run the specialized agent
    try:
        # VULN-AI-02: req.user_id is client-supplied — no server-side session validation
        # jwt_user_id is extracted from the JWT (empty if unauthenticated)
        agent = build_agent(intent, mcp_router, session_id, req.user_id, jwt_user_id=jwt_user_id)
        response_text = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None, lambda: agent.run(req.message)
            ),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Agent timed out session=%s agent=%s", session_id, agent_name)
        response_text = "The request timed out. Please try again."
        agent_name = "timeout"
    except LLMUpstreamError as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        headers = {"X-LLM-Error-Type": exc.error_type}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        if exc.request_id:
            headers["X-LLM-Request-ID"] = exc.request_id
        logger.warning(
            "chat upstream failure session=%s agent=%s intent=%s status=%s "
            "type=%s retry_after=%s request_id=%s latency_ms=%.1f",
            session_id,
            agent_name,
            intent,
            exc.status_code,
            exc.error_type,
            exc.retry_after,
            exc.request_id,
            latency_ms,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": exc.error_type,
                "message": exc.message,
                "retry_after": exc.retry_after,
                "request_id": exc.request_id,
            },
            headers=headers,
        ) from exc
    except Exception as exc:
        logger.exception("Agent failed for session=%s: %s", session_id, exc)
        response_text = "I encountered an error processing your request. Please try again."
        agent_name = "error"

    latency_ms = (time.monotonic() - t0) * 1000

    # Store session ownership for access control on history endpoint
    if jwt_user_id and session_id not in _SESSION_OWNERS:
        _SESSION_OWNERS[session_id] = jwt_user_id

    # VULN-AUTH-09: Store full message + response in session history (no TTL)
    if session_id not in _SESSION_HISTORY:
        _SESSION_HISTORY[session_id] = []
    _SESSION_HISTORY[session_id].append({
        "role":       "user",
        "content":    req.message,
        "timestamp":  time.time(),
        "user_id":    req.user_id,
    })
    _SESSION_HISTORY[session_id].append({
        "role":       "assistant",
        "agent":      agent_name,
        "content":    response_text,
        "timestamp":  time.time(),
        "latency_ms": round(latency_ms, 1),
    })

    # Broadcast completion event
    await broadcast_mgr.broadcast({
        "type": "agent_response",
        "session_id": session_id,
        "agent": agent_name,
        "intent": intent,
        "response_length": len(response_text),
        "latency_ms": round(latency_ms, 1),
        "timestamp": time.time(),
    })

    # VULN-AUTH-10: Fire registered webhooks with full response payload (no URL validation)
    if _REGISTERED_WEBHOOKS:
        webhook_payload = {
            "event":      "chat.response",
            "session_id": session_id,
            "user_id":    req.user_id,
            "agent":      agent_name,
            "message":    req.message,
            "response":   response_text,
            "timestamp":  time.time(),
        }
        loop = asyncio.get_running_loop()
        for wh in list(_REGISTERED_WEBHOOKS.values()):
            if "chat.response" in wh.get("events", []):
                url = wh["url"]
                # VULN-AUTH-10: raw requests.post to any URL — no SSRF protection
                loop.run_in_executor(
                    None,
                    lambda u=url, p=webhook_payload: _requests.post(
                        u, json=p, timeout=5, allow_redirects=False
                    ),
                )

    return ChatResponse(
        session_id=session_id,
        intent=intent,
        agent=agent_name,
        agent_type=agent_name,
        response=response_text,
        latency_ms=round(latency_ms, 1),
        access_token=_inline_tokens["access_token"] if _inline_tokens else "",
        refresh_token=_inline_tokens["refresh_token"] if _inline_tokens else "",
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint â€” agent debug log stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/agent-logs")
async def ws_agent_logs(websocket: WebSocket) -> None:
    """Stream real-time agent routing and tool execution events as JSON."""
    await broadcast_mgr.connect(websocket)
    await websocket.send_text(json.dumps({
        "type": "connected",
        "message": "Pinnacle Bank Agent Log Stream â€” Multi-Agent Debug Panel",
        "agents": [a["name"] for a in AGENT_REGISTRY],
        "timestamp": time.time(),
    }))
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping", "timestamp": time.time()}))
    except WebSocketDisconnect:
        await broadcast_mgr.disconnect(websocket)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "orchestrator.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8001")),
        reload=False,
    )
