# Example 04: Tool Argument Validation & Modification

This example showcases the use of `before_tool_callback` to validate arguments intended for a tool and to modify them for consistency or standardization before the tool is executed.

## Callbacks Showcased

- `before_tool_callback`: Executed before a tool (defined in the agent) is called. It allows inspection and modification of the arguments, or even skipping the tool call by returning a result directly.

## Purpose

The main objectives are to demonstrate how `before_tool_callback` can:

- **Validate Tool Inputs**: Check if the arguments provided for a tool meet certain criteria (e.g., correct date format).
- **Prevent Tool Errors**: If validation fails, the callback can prevent the tool from being called with invalid data, potentially returning an error message directly to the LLM (which then formulates a user response).
- **Standardize Arguments**: Modify arguments to a consistent format (e.g., converting "afternoon" to "14:00").
- Make tool interactions more robust and reliable.

The agent in this example is a meeting scheduler that uses a `schedule_meeting_tool`.

## How to Test

1.  Navigate to the root of this project if you are not already there.
2.  Change to the specific example directory:
    ```bash
    cd 7-agents-and-callbacks/example_04_tool_arg_validation_modification
    ```
3.  Run the agent using the ADK web server:
    ```bash
    adk web
    ```
4.  Open your web browser and navigate to the URL provided by the `adk web` command (usually `http://127.0.0.1:8000`).
5.  Interact with the "meeting_scheduler_agent". Try the following scenarios:

    - **Invalid Date Format**: "Schedule a meeting for 25/12/2025 about 'Project Review' with ['Alice', 'Bob'] for the afternoon."
    - **Missing Date**: "Schedule a meeting about 'Quick Sync' with ['Charlie'] for 10am."
    - **Time Preference Modification (Afternoon)**: "Schedule a meeting for 2025-12-26 about 'Team Lunch' with ['Dave', 'Eve'] for the afternoon."
    - **Time Preference Modification (Morning)**: "Schedule a meeting for 2025-12-27 about 'Planning Session' with ['Frank'] for the morning."
    - **Valid Input**: "Schedule a meeting for 2025-12-28 about 'Client Demo' with ['Grace', 'Heidi'] for 15:00."

## What to Observe

- **Console Output**:
  - `[BEFORE TOOL]` logs will show the original arguments the LLM decided to pass to the `schedule_meeting_tool`.
  - **Invalid Date/Missing Date**:
    - You'll see a log like `[BEFORE TOOL] Invalid date format: 25/12/2025. Blocking call.` or `[BEFORE TOOL] Meeting date not provided. Blocking call.`
    - The tool itself (`schedule_meeting_tool`) will _not_ be executed in these cases. The callback returns an error dictionary.
  - **Time Preference Modification**:
    - If you use "afternoon" or "morning", you'll see a log like `[BEFORE TOOL] Modified 'time' argument from 'afternoon' to '14:00'`.
    - The `[TOOL EXECUTED]` log for `schedule_meeting_tool` will then show the modified time (e.g., "14:00" or "10:00").
  - **Valid Input**: The `before_tool_callback` will log the arguments, make no changes (if already valid), and the tool will execute with these arguments.
- **Agent's Response in UI**:
  - **Invalid Date/Missing Date**: The agent will respond with an error message based on the dictionary returned by the `before_tool_callback` (e.g., "Error: Please provide the meeting date in YYYY-MM-DD format.").
  - **Time Preference Modification/Valid Input**: The agent will confirm the meeting, and the confirmation will reflect any modifications made by the callback (e.g., "Meeting ... scheduled for 2025-12-26 at 14:00...").
