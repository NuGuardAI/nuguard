"""FinTech GOAT — MCP Scheduler — DELIBERATELY VULNERABLE."""
from __future__ import annotations
import json, logging, os, uuid
from typing import Any
import requests
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("mcp_scheduler")
logging.basicConfig(level=logging.INFO)
mcp = FastMCP(name="fintech-scheduler", instructions="Pinnacle Bank scheduler tool API.")

_TASKS: dict[str, dict] = {
    "TASK-001": {"task_id": "TASK-001", "task_type": "report", "params": {"report_type": "monthly", "user_id": "ACCT-001"}, "run_at": "2026-04-14T06:00:00Z", "created_by": "ACCT-001", "status": "scheduled"},
    "TASK-002": {"task_id": "TASK-002", "task_type": "transfer", "params": {"from": "ACCT-002", "to": "ACCT-009", "amount": 1500.00}, "run_at": "2026-04-15T09:00:00Z", "created_by": "ACCT-002", "status": "scheduled"},
    "TASK-003": {"task_id": "TASK-003", "task_type": "notification", "params": {"user_id": "ACCT-003", "message": "Your quarterly statement is ready", "channel": "email"}, "run_at": "2026-04-14T08:00:00Z", "created_by": "system", "status": "scheduled"},
    "TASK-004": {"task_id": "TASK-004", "task_type": "data_export", "params": {"customer_id": "ACCT-007", "format": "csv", "include_pii": True}, "run_at": "2026-04-16T02:00:00Z", "created_by": "admin", "status": "scheduled"},
    "TASK-005": {"task_id": "TASK-005", "task_type": "report", "params": {"report_type": "quarterly", "user_id": "ACCT-005", "date_from": "2026-01-01", "date_to": "2026-03-31"}, "run_at": "2026-04-30T06:00:00Z", "created_by": "ACCT-005", "status": "scheduled"},
}


@mcp.tool()
async def schedule_task(task_type: str, params: dict, run_at: str, created_by: str = "") -> dict:
    valid_types = {"report", "transfer", "notification", "data_export"}
    if task_type not in valid_types:
        return {"error": f"Invalid task_type '{task_type}'", "valid_types": list(valid_types)}
    task_id = f"TASK-{uuid.uuid4().hex[:6].upper()}"
    task = {
        "task_id": task_id,
        "task_type": task_type,
        "params": params,
        "run_at": run_at,
        "created_by": created_by or "anonymous",
        "status": "scheduled",
    }
    _TASKS[task_id] = task
    logger.info("Task scheduled: %s type=%s run_at=%s by=%s", task_id, task_type, run_at, created_by)
    return task


@mcp.tool()
async def list_scheduled_tasks(user_id: str = "") -> list:
    # VULN: empty user_id lists ALL scheduled tasks across all users without auth
    if user_id:
        return [t for t in _TASKS.values() if t["created_by"] == user_id]
    return list(_TASKS.values())


@mcp.tool()
async def cancel_task(task_id: str) -> dict:
    # VULN: cancels any task without ownership check
    task = _TASKS.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    _TASKS[task_id]["status"] = "cancelled"
    logger.warning("Task %s cancelled without ownership check", task_id)
    return {"task_id": task_id, "status": "cancelled"}


@mcp.tool()
async def run_task_immediately(task_id: str, override_by: str = "system") -> dict:
    # VULN-MCP-27: immediately executes ANY scheduled task — no ownership or auth check
    task = _TASKS.get(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    task_type = task.get("task_type", "unknown")
    p = task.get("params", {})
    result: dict[str, Any] = {}
    if task_type == "transfer":
        result = {
            "transfer_id": f"TXN-{uuid.uuid4().hex[:8].upper()}",
            "from": p.get("from"),
            "to": p.get("to"),
            "amount": p.get("amount"),
            "status": "completed",
            "executed_at": "2026-04-13T00:00:00Z",
        }
    elif task_type == "data_export":
        result = {
            "export_id": f"EXP-{uuid.uuid4().hex[:8].upper()}",
            "customer_id": p.get("customer_id"),
            "records": 150,
            "format": p.get("format", "json"),
            "download_url": "http://internal-storage/exports/dump.json",
        }
    elif task_type == "report":
        result = {
            "report_id": f"RPT-{uuid.uuid4().hex[:8].upper()}",
            "report_type": p.get("report_type"),
            "user_id": p.get("user_id"),
            "status": "generated",
            "rows": 42,
        }
    else:
        result = {"status": "executed", "task_type": task_type, "params": p}

    _TASKS[task_id]["status"] = "executed"
    logger.warning("Task %s executed immediately by %s", task_id, override_by)
    return {
        "task_id": task_id,
        "task_type": task_type,
        "executed": True,
        "override_by": override_by,
        "result": result,
    }


@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "scheduler"})


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
        if tool_name == "schedule_task":
            result = await schedule_task(**arguments)
        elif tool_name == "list_scheduled_tasks":
            result = await list_scheduled_tasks(**arguments)
        elif tool_name == "cancel_task":
            result = await cancel_task(**arguments)
        elif tool_name == "run_task_immediately":
            result = await run_task_immediately(**arguments)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})
    except Exception as exc:
        logger.error("tools/call tool=%s: %s", tool_name, exc)
        return JSONResponse({"jsonrpc": "2.0", "id": call_id, "error": {"code": -32603, "message": str(exc)}}, status_code=500)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
