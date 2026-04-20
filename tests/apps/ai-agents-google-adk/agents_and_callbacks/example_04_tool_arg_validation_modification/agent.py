# agent.py in 04_tool_argument_validation_modification
from datetime import datetime
from typing import Any, Dict, Optional

from google.adk.agents import Agent as LlmAgent
from google.adk.tools.base_tool import BaseTool  # Required for type hinting in callback
from google.adk.tools.tool_context import ToolContext
from google.genai import types


def schedule_meeting_tool(
    meeting_date: str, topic: str, attendees: list[str], time: str
) -> Dict[str, Any]:
    """Schedules a meeting on a specific date, time, with a topic and attendees.
    Args:
    meeting_date: The date of the meeting in YYYY-MM-DD format.
    topic: The topic of the meeting.
    attendees: A list of attendee names.
    time: The preferred time or time slot (e.g., '10:00', 'afternoon').
    """
    print(
        f"\n[TOOL EXECUTED] schedule_meeting_tool with date: {meeting_date}, topic: {topic}, attendees: {attendees}, time: {time}\n"
    )
    return {
        "status": "success",
        "message": f"Meeting about '{topic}' scheduled for {meeting_date} at {time} with {', '.join(attendees)}.",
    }


def before_tool_callback_schedule(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext
) -> Optional[Dict[str, Any]]:
    tool_name = tool.name
    print(f"\n[BEFORE TOOL] Calling '{tool_name}' with original args: {args}")

    if tool_name == "schedule_meeting_tool":
        meeting_date = args.get("meeting_date")
        time_preference = args.get(
            "time", ""
        ).lower()  # 'time' is what the tool expects

        # Date Validation
        if meeting_date:
            try:
                datetime.strptime(meeting_date, "%Y-%m-%d")
            except ValueError:
                print(
                    f"[BEFORE TOOL] Invalid date format: {meeting_date}. Blocking call."
                )
                return {
                    "result": "Error: Please provide the meeting date in YYYY-MM-DD format."
                }  # Return as tool output
        else:
            print("[BEFORE TOOL] Meeting date not provided. Blocking call.")
            return {"result": "Error: Meeting date is required."}

        # Time Modification (Example)
        if "afternoon" in time_preference:
            args["time"] = "14:00"  # Standardize to a specific time
            print(
                f"[BEFORE TOOL] Modified 'time' argument from '{time_preference}' to '14:00'"
            )
        elif "morning" in time_preference:
            args["time"] = "10:00"
            print(
                f"[BEFORE TOOL] Modified 'time' argument from '{time_preference}' to '10:00'"
            )

    print("\n")
    return None


meeting_scheduler_agent = LlmAgent(
    name="meeting_scheduler_agent",
    description="An agent that schedules meetings and validates inputs.",
    tools=[schedule_meeting_tool],
    model="gemini-2.0-flash",
    instruction="You are a meeting scheduling assistant. When asked to schedule a meeting, gather the date (YYYY-MM-DD), topic, attendees (as a list), and a time preference (e.g., '10:00 AM', 'afternoon', 'morning'). Then use the 'schedule_meeting_tool'.",
    before_tool_callback=before_tool_callback_schedule,
)

root_agent = meeting_scheduler_agent
