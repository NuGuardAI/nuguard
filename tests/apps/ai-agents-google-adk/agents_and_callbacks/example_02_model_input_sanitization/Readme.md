# Example 02: Model Input Sanitization

This example demonstrates the use of `before_model_callback` to sanitize user input by redacting mock sensitive information (PII) before it is sent to the Large Language Model (LLM).

## Callbacks Showcased

- `before_model_callback`: Executed just before the request is sent to the LLM. This callback allows modification of the `LlmRequest` object.

## Purpose

The goal is to show how to:

- Intercept user input that is about to be processed by the LLM.
- Identify and redact sensitive data (e.g., mock credit card numbers, Social Security Numbers) using regular expressions.
- Protect user privacy and comply with data handling policies by preventing sensitive information from reaching the LLM or being logged insecurely.

This agent acts as a general knowledge assistant but sanitizes input first.

## How to Test

1.  Navigate to the root of this project if you are not already there.
2.  Change to the specific example directory:
    ```bash
    cd 7-agents-and-callbacks/example_02_model_input_sanitization
    ```
3.  Run the agent using the ADK web server:
    ```bash
    adk web
    ```
4.  Open your web browser and navigate to the URL provided by the `adk web` command (usually `http://127.0.0.1:8000`).
5.  Interact with the "input_sanitizer_agent". Try sending messages that include mock PII. For example:
    - "My credit card is 1234-5678-9012-3456, can you tell me about it?"
    - "My SSN is 000-11-2222, and I want to know about the history of New York."

## What to Observe

- **Console Output**:
  - You will see a `[BEFORE MODEL]` log showing the "Original user input" exactly as you typed it.
  - If PII is detected, subsequent `[BEFORE MODEL]` logs will indicate which type of PII was redacted (e.g., "Redacted CREDIT_CARD").
  - Finally, a `[BEFORE MODEL]` log will display the "Sanitized input to LLM", where the mock PII has been replaced with `[REDACTED PII]`.
- **Agent's Response**: The LLM will respond based on the _sanitized_ input. For instance, if you asked a question along with PII, the LLM will only "see" the question part and `[REDACTED PII]`, not the actual PII.
- If you send input without any PII patterns, the console will still show the original input, but no redaction messages will appear, and the input to the LLM will be unchanged.
