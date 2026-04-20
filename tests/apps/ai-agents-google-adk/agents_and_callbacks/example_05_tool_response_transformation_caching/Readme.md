# Example 05: Tool Response Transformation & Caching

This example demonstrates using `before_tool_callback` for caching results from a tool and `after_tool_callback` for transforming the tool's raw output into a more user-friendly format and for populating the cache. State modifications for caching are handled by directly updating `tool_context.state`, aligning with ADK best practices.

## Callbacks Showcased

- `before_tool_callback`:
  - Checks if a valid cached response exists in `tool_context.state["app_cache"]` for the current tool call and its arguments.
  - If a fresh cached response is found, it returns this response (via `{"cached_result": ...}`), skipping the actual tool execution.
  - Manages cache expiry by removing stale entries from `tool_context.state["app_cache"]`.
- `after_tool_callback`:
  - Takes the raw output from the tool.
  - Transforms this output into a more structured or user-friendly format for the LLM.
  - Stores the original tool's response in `tool_context.state["app_cache"]` for future identical requests.

## Purpose

The goals are to illustrate how to:

- **Improve Efficiency**: Reduce redundant calls to tools (simulating external APIs) by caching their responses.
- **Manage State for Caching**: Use `tool_context.state` (specifically `tool_context.state["app_cache"]`) to store and retrieve cached data. The ADK framework automatically handles persisting these changes.
- **Enhance Tool Output**: Format the raw data from a tool into a more presentable string or structured dictionary before the LLM uses it to generate a user-facing response.
- **Handle Cache Expiry**: Implement a simple time-based expiry for cached items.

The agent in this example is a currency converter that uses a `convert_currency_tool` with mock exchange rates.

## How to Test

1.  Navigate to the root of this project if you are not already there.
2.  Change to the specific example directory:
    ```bash
    cd 7-agents-and-callbacks/example_05_tool_response_transformation_caching
    ```
3.  Run the agent using the ADK web server:
    ```bash
    adk web
    ```
4.  Open your web browser and navigate to the URL provided by the `adk web` command (usually `http://127.0.0.1:8000`).
5.  Interact with the "currency_converter_agent".

    - **First Conversion**: Ask to convert a currency, e.g., "How much is 100 USD in EUR?"
    - **Second Conversion (Cached)**: Within 10 seconds (the `CACHE_EXPIRY_SECONDS`), ask the _exact same_ conversion again: "How much is 100 USD in EUR?"
    - **Different Conversion**: Ask for a different conversion, e.g., "Convert 50 GBP to USD."
    - **Third Conversion (Expired Cache)**: Wait for more than 10 seconds, then ask the first conversion again: "How much is 100 USD in EUR?"

## What to Observe

- **Console Output**:

  - **First Conversion**:
    - `[BEFORE TOOL]` logs: `[CACHE DEBUG] Cache MISS...`
    - `[TOOL EXECUTED] convert_currency_tool...` (the actual tool runs).
    - `[AFTER TOOL]` logs:
      - Shows the original tool response.
      - `[AFTER TOOL] Updated tool_context.state['app_cache'] to: {...}` showing the new cache content.
      - Log showing the formatted result for the LLM.
  - **Second Conversion (Cached Call within 10s)**:
    - `[BEFORE TOOL]` logs: `[CACHE DEBUG] Found entry... Age: Xs...`, `[CACHE DEBUG] Using FRESH CACHED result...`
    - The `[TOOL EXECUTED]` log for `convert_currency_tool` will **not** appear.
    - The `after_tool_callback` for this specific tool invocation will be skipped as `before_tool_callback` returned a cached result.
  - **Different Conversion**: Similar flow to the first conversion, but with a new cache key and corresponding updates to `tool_context.state["app_cache"]`.
  - **Third Conversion (Expired Cache after >10s)**:
    - `[BEFORE TOOL]` logs: `[CACHE DEBUG] Found entry... Age: >10s...`, `[CACHE DEBUG] Cache EXPIRED...`, `[CACHE DEBUG] Prepared deletion...`
    - `[BEFORE TOOL] Updated tool_context.state['app_cache'] to: {...}` (showing the cache with the key removed).
    - `[TOOL EXECUTED] convert_currency_tool...` (the tool runs again).
    - `[AFTER TOOL]` logs will show the response being processed and `tool_context.state["app_cache"]` being updated again with the fresh result.

- **Agent's Response in UI**:
  - The agent will provide the converted currency amount.
  - The response format will be user-friendly, e.g., "100.00 USD is approximately 92.00 EUR (Rate: 0.92)." This formatted string comes from the `result_summary` created in `after_tool_callback_format_and_cache`.
  - Whether the result came from a fresh tool call or the cache, the user-facing response should be consistent # Using this import path as per original example 05.
