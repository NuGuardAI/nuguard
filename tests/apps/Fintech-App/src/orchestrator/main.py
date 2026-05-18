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
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agents import (
    AGENT_REGISTRY,
    _ALL_TOOL_DEFS,
    _ACCOUNT_REGISTRY,
    _INTENT_TO_AGENT,
    build_agent,
    build_wealth_advisor_agent,
    triage_intent,
)
from .mcp_client import MCPClient
from .mcp_router import MCPRouter
from .telemetry import setup_telemetry

# ---------------------------------------------------------------------------
# Logging -- configure before setup_telemetry so early warnings are captured.
# force=True reinstalls handlers after uvicorn calls dictConfig.
# ---------------------------------------------------------------------------
_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    force=True,
)

# ---------------------------------------------------------------------------
# Telemetry bootstrapping (after logging is configured)
# ---------------------------------------------------------------------------
setup_telemetry()
logger = logging.getLogger("orchestrator.main")

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
mcp_client = MCPClient()   # legacy â€” kept for any remaining compat shims

_DEMO_PASSWORD = os.getenv("APP_PASSWORD", "demo123")
_EMAIL_TO_USER_ID = {
    str(account.get("email", "")).strip().lower(): user_id
    for user_id, account in _ACCOUNT_REGISTRY.items()
    if str(account.get("email", "")).strip()
}


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Agent orchestrator starting up â€” %d agents registered", len(AGENT_REGISTRY))
    yield
    logger.info("Agent orchestrator shutting down â€¦")


app = FastAPI(
    title="Pinnacle Bank Agent Orchestrator",
    description="Multi-agent AI orchestrator â€” 6 specialized banking agents",
    version="2.0.0",
    lifespan=lifespan,
)

# NOTE: Permissive CORS â€” intentional for demo purposes (also logged as finding)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _request_logger(request: Request, call_next):
    """Log every HTTP request with method, path, status, and latency."""
    t0 = time.monotonic()
    response = await call_next(request)
    latency_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "http %s %s -> %d  %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    user_id: str = ""   # client-supplied â€” used to build agent context (VULN-AI-02)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    agent: str
    agent_type: str     # which of the 6 specialized agents handled this request
    response: str
    latency_ms: float


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


@app.post("/api/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    """Authenticate a demo user and mint a bearer token bound to that user."""
    user_id = _EMAIL_TO_USER_ID.get(req.username.strip().lower())
    if not user_id or req.password != _DEMO_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return LoginResponse(access_token=user_id, user_id=user_id)


def _resolve_authenticated_user_id(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if token in _ACCOUNT_REGISTRY:
        return token
    return None


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    """Process a chat message through the triage â†’ specialized agent pipeline.

    Flow: triage_intent â†’ build_agent â†’ agent.run() â†’ ChatResponse
    VULN-AI-06: triage is keyword-based â€” manipulable via injected intent words.
    VULN-AI-02: req.user_id is client-supplied â€” no server-side session check.
    """
    session_id = req.session_id or str(uuid.uuid4())
    t0 = time.monotonic()
    authenticated_user_id = _resolve_authenticated_user_id(request)
    effective_user_id = authenticated_user_id or req.user_id

    # Step 1: Triage â€” classify intent
    # VULN-AI-06: classification uses LLM keyword matching; embed "fraud detection"
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
        # VULN-AI-02: req.user_id is client-supplied â€” no server-side session validation
        agent = build_agent(intent, mcp_router, session_id, effective_user_id)
        response_text = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None, lambda: agent.run(req.message)
            ),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.warning(
            "Agent orchestrator timeout session=%s agent=%s elapsed_ms=%.0f",
            session_id, agent_name, elapsed_ms,
        )
        response_text = (
            "Your request is taking longer than expected and has timed out. "
            "This may be due to high demand on the AI service. "
            "Please try again in a moment or contact Pinnacle Bank support."
        )
        agent_name = "timeout"
    except Exception as exc:
        logger.exception("Agent failed for session=%s: %s", session_id, exc)
        response_text = "I encountered an error processing your request. Please try again."
        agent_name = "error"

    latency_ms = (time.monotonic() - t0) * 1000

    # Broadcast completion event
    await broadcast_mgr.broadcast({
        "type": "agent_response",
        "session_id": session_id,
        "agent": agent_name,
        "intent": intent,
        "response_length": len(response_text),
        "latency_ms": round(latency_ms, 1),
        "timed_out": agent_name == "timeout",
        "timestamp": time.time(),
    })

    return ChatResponse(
        session_id=session_id,
        intent=intent,
        agent=agent_name,
        agent_type=agent_name,
        response=response_text,
        latency_ms=round(latency_ms, 1),
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
