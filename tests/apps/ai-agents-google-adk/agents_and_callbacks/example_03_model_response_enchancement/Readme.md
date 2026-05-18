# Example 03: Model Response Enhancement

This example demonstrates how to use the `after_model_callback` to parse the LLM's response, extract structured information, and enhance the response by adding helpful content like quick links before it's sent to the user.

## Callbacks Showcased

- `after_model_callback`: Executed after the LLM has generated a response but before it's sent back to the user. This callback allows modification of the `LlmResponse` object.

## Purpose

The primary goals are to:

- **Extract Structured Data**: Parse free-form text from the LLM to find and extract specific pieces of information (e.g., flight details like flight number, origin, destination, date).
- **Enrich Responses**: Dynamically add relevant information or actions, such as quick links to policy pages, based on the content of the LLM's response.
- Improve user experience by making the agent's output more structured and actionable.

This agent acts as a travel assistant.

## How to Test

1.  Navigate to the root of this project if you are not already there.
2.  Change to the specific example directory:
    ```bash
    cd 7-agents-and-callbacks/example_03_model_response_enhancement
    ```
3.  Run the agent using the ADK web server:
    ```bash
    adk web
    ```
4.  Open your web browser and navigate to the URL provided by the `adk web` command (usually `http://127.0.0.1:8000`).
5.  Interact with the "travel_response_enhancer_agent". Try the following:

    - **Book a flight**: Ask the agent to book a flight. For example: "Book me a flight from London to Paris for 2025-12-25."
      The LLM is instructed to make up flight details.
    - **Ask about policies**: Ask questions that might trigger the addition of quick links. For example: "What is your refund policy?" or "Tell me about baggage allowance."

## What to Observe

- **Console Output**:
  - You will see an `[AFTER MODEL]` log showing the "Original LLM response".
  - **For flight booking**: If the LLM's response contains flight information in the expected format, you'll see a log like `[AFTER MODEL] Extracted flight info: {'flight_number': '...', ...}`.
  - **For policy questions**: If the LLM's response mentions "refund policy" or "baggage allowance", you'll see logs like `[AFTER MODEL] Added refund policy link.`
  - An `[AFTER MODEL]` log will show the "Enhanced response being sent to user" if any modifications were made. Otherwise, it will state "No enhancements made".
- **Agent's Response in UI**:
  - **For flight booking**: The agent's response will include the LLM's confirmation, and appended to that will be a "Flight Summary Logged" section with the extracted details.
  - **For policy questions**: The agent's response will include the LLM's answer, and appended to that will be markdown links like "[Refund Policy](https://example.com/refunds)".
- The `callback_context.state["extracted_flight_info"]` will store the extracted details, demonstrating how data can be persisted or used for other purposes within the agent's session state (though this example primarily shows it by adding to the response text).
