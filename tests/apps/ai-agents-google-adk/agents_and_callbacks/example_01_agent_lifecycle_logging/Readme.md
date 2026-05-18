# Example 01: Agent Lifecycle Logging

This example demonstrates how to use `before_agent_callback` and `after_agent_callback` to log detailed information about an agent's interaction lifecycle.

## Callbacks Showcased

- `before_agent_callback`: Executed before the main agent logic processes the user's request.
- `after_agent_callback`: Executed after the agent has finished processing and is about to send a response.

## Purpose

The primary goal is to illustrate how these callbacks can be used for:

- **Session Tracking**: Assigning and using a unique session ID for each conversation.
- **Interaction Logging**: Recording when each interaction starts and ends.
- **Performance Metrics**: Calculating and logging the duration of each interaction.
- **Request Counting**: Keeping track of the number of requests within a session.

This is useful for debugging, monitoring agent performance, and gathering analytics.

## How to Test

1.  Navigate to the root of this project if you are not already there.
2.  Change to the specific example directory:
    ```bash
    cd 7-agents-and-callbacks/example_01_agent_lifecycle_logging
    ```
3.  Run the agent using the ADK web server:
    ```bash
    adk web
    ```
4.  Open your web browser and navigate to the URL provided by the `adk web` command (usually `http://127.0.0.1:8000`).
5.  Interact with the "lifecycle_logger_agent". Since it's an echo agent, it will repeat your messages. Send a few messages.

## What to Observe

- **Console Output**: Look at the terminal where you ran `adk web`.
  - Before each of your messages is processed by the agent, you'll see a `[BEFORE AGENT - SID: <session_id>]` log. This log will include the interaction number and the start timestamp.
  - After the agent responds, you'll see an `[AFTER AGENT - SID: <session_id>]` log. This will show the interaction number and the total duration of the interaction.
  - Notice that the `session_id` remains consistent across multiple interactions in the same session.
  - The `request_counter` will increment with each message you send.
- **Agent Behavior**: The agent will simply echo back what you type. The main functionality to observe is the logging messages in the console.
