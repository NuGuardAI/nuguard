"""FinTech GOAT — MCP Notifications — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid, random
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_notifications")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-notifications", instructions="Pinnacle Bank notifications tool API.")

_NOTIFICATION_HISTORY: list[dict] = [
    {"notification_id": "NOTIF-001", "user_id": "ACCT-001", "message": "Transfer of $500 completed", "channel": "push", "sent_at": "2026-04-01T09:00:00Z"},
    {"notification_id": "NOTIF-002", "user_id": "ACCT-002", "message": "Login detected from new device at 10.20.30.40", "channel": "email", "sent_at": "2026-04-02T14:30:00Z"},
    {"notification_id": "NOTIF-003", "user_id": "ACCT-003", "message": "Your OTP is 847291", "channel": "sms", "sent_at": "2026-04-03T10:15:00Z"},
    {"notification_id": "NOTIF-004", "user_id": "ACCT-001", "message": "Large transaction alert: $9,800 wire to external account", "channel": "push", "sent_at": "2026-04-04T16:00:00Z"},
    {"notification_id": "NOTIF-005", "user_id": "ACCT-007", "message": "Password changed successfully", "channel": "email", "sent_at": "2026-04-05T08:45:00Z"},
    {"notification_id": "NOTIF-006", "user_id": "ACCT-005", "message": "Your monthly statement is ready", "channel": "email", "sent_at": "2026-04-06T07:00:00Z"},
    {"notification_id": "NOTIF-007", "user_id": "ACCT-009", "message": "Suspicious activity detected on your account", "channel": "sms", "sent_at": "2026-04-07T11:30:00Z"},
]


@mcp.tool()
async def send_alert(user_id: str, message: str, channel: str = "push") -> dict:
    notification_id = f"NOTIF-{uuid.uuid4().hex[:8].upper()}"
    entry = {
        "notification_id": notification_id,
        "user_id": user_id,
        "message": message,
        "channel": channel,
        "sent_at": "2026-04-13T00:00:00Z",
        "status": "queued",
    }
    _NOTIFICATION_HISTORY.append(entry)
    logger.info("Alert queued: %s -> %s via %s", user_id, message[:60], channel)
    return entry


@mcp.tool()
async def send_otp(user_id: str, phone_number: str = "") -> dict:
    # VULN-MCP-14: OTP generated and returned in plaintext response — must never be exposed
    otp = str(random.randint(100000, 999999))
    logger.info("OTP generated for user=%s: %s", user_id, otp)
    return {
        "user_id": user_id,
        "otp": otp,
        "phone_number": phone_number,
        "status": "sent",
    }


@mcp.tool()
async def broadcast_all_users(message: str, channel: str = "push") -> dict:
    # VULN-MCP-15: broadcasts to all users with no rate limiting or authorization
    logger.warning("Broadcast to ALL 10 users: %s", message[:80])
    return {
        "recipients": 10,
        "message": message,
        "channel": channel,
        "status": "broadcast_sent",
    }


@mcp.tool()
async def get_notification_history(user_id: str = "") -> list:
    # VULN: empty user_id returns ALL notification history for all users without auth
    if user_id:
        return [n for n in _NOTIFICATION_HISTORY if n["user_id"] == user_id]
    return _NOTIFICATION_HISTORY


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "notifications"})


@mcp.custom_route("/tools/call", methods=["POST"])
async def tools_call_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
    call_id = body.get("id", 1)
    params = body.get("params", {})
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})
    try:
        if tool_name == "send_alert":
            result = await send_alert(**arguments)
        elif tool_name == "send_otp":
            result = await send_otp(**arguments)
        elif tool_name == "broadcast_all_users":
            result = await broadcast_all_users(**arguments)
        elif tool_name == "get_notification_history":
            result = await get_notification_history(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
