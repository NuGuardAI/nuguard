# agent.py in 01_agent_lifecycle_logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from google.adk.agents import Agent as LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types


def before_agent_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    if "session_id" not in callback_context.state:
        callback_context.state["session_id"] = str(uuid.uuid4())

    callback_context.state["interaction_start_time"] = datetime.now(timezone.utc)
    request_num = callback_context.state.get("request_counter", 0) + 1
    callback_context.state["request_counter"] = request_num

    print(
        f"\n[BEFORE AGENT - SID: {callback_context.state['session_id']}] Interaction #{request_num} initiated."
    )
    print(f"Timestamp: {callback_context.state['interaction_start_time']}")
    print("\n\n")
    # Here you could also log incoming user_id if passed via context
    return None


def after_agent_callback(callback_context: CallbackContext) -> Optional[types.Content]:
    start_time = callback_context.state.get("interaction_start_time")
    duration_str = "N/A"
    if start_time:
        duration = datetime.now(timezone.utc) - start_time
        duration_str = f"{duration.total_seconds():.2f}s"

    print(
        f"\n[AFTER AGENT - SID: {callback_context.state['session_id']}] Interaction #{callback_context.state['request_counter']} completed."
    )
    print(f"Duration: {duration_str}")
    print("\n\n")
    # Potentially log final response or any errors encountered
    # callback_context.state can be used to persist metrics for the session
    return None


lifecycle_logger_agent = LlmAgent(
    name="lifecycle_logger_agent",
    description="An agent that logs its interaction lifecycle.",
    model="gemini-2.0-flash",
    instruction="You are an echo agent. Repeat the user's message.",
    before_agent_callback=before_agent_callback,
    after_agent_callback=after_agent_callback,
)

root_agent = lifecycle_logger_agent
