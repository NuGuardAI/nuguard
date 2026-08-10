"""
OpenTelemetry + Application Insights telemetry helpers for the orchestrator.

Intentionally logs sensitive data (full user messages, routing decisions with
message fragments) to Application Insights custom events.  This is VULN-06.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace

_configured = False
logger = logging.getLogger("orchestrator.telemetry")


def setup_telemetry(service_name: str = "agent-orchestrator") -> None:
    """Bootstrap Azure Monitor OpenTelemetry exporter."""
    global _configured
    if _configured:
        return

    conn_str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if conn_str:
        configure_azure_monitor(
            connection_string=conn_str,
            service_name=service_name,
        )
        logger.info("Azure Monitor telemetry configured for %s", service_name)
    else:
        logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry disabled")

    _configured = True


def record_routing_event(
    from_agent: str,
    to_agent: str,
    classified_intent: str,
    user_message: str,
    session_id: str,
) -> None:
    """Emit an AgentRoutingDecision event to Application Insights.

    >>> VULN-06: user_message (full unredacted content) is recorded in telemetry.
    >>> This means PII, financial details, and any injected payloads are persisted
    >>> in the Log Analytics workspace and queryable via KQL.
    """
    span = trace.get_current_span()
    span.set_attribute("agent.routing.from", from_agent)
    span.set_attribute("agent.routing.to", to_agent)
    span.set_attribute("agent.routing.intent", classified_intent)
    span.set_attribute("agent.session_id", session_id)

    logger.info(
        "AgentRoutingDecision",
        extra={
            "custom_dimensions": {
                "event_name": "AgentRoutingDecision",
                "from_agent": from_agent,
                "to_agent": to_agent,
                "classified_intent": classified_intent,
                "session_id": session_id,
                # Intentional: full user message stored alongside routing decision
                "user_message": user_message,
            }
        },
    )


def record_tool_event(
    tool_name: str,
    tool_args: dict[str, Any],
    result: Any,
    session_id: str,
) -> None:
    """Emit an MCPToolExecutionAttempt event (orchestrator view).

    Raw tool_args are logged without sanitisation (VULN-06 continuation).
    """
    span = trace.get_current_span()
    span.set_attribute("event.name", "MCPToolExecutionAttempt")
    span.set_attribute("mcp.tool.name", tool_name)
    span.set_attribute("mcp.tool.args_raw", json.dumps(tool_args))

    logger.info(
        "MCPToolExecutionAttempt",
        extra={
            "custom_dimensions": {
                "event_name": "MCPToolExecutionAttempt",
                "tool_name": tool_name,
                "tool_args": json.dumps(tool_args),
                "result_snippet": str(result)[:512],
                "session_id": session_id,
            }
        },
    )
