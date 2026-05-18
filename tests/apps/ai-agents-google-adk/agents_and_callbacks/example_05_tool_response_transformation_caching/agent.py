# agent.py in example_05_tool_response_transformation_caching

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from google.adk.agents import Agent as LlmAgent
from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

MOCK_RATES = {
    ("USD", "EUR"): 0.92,
    ("EUR", "USD"): 1.08,
    ("GBP", "USD"): 1.27,
    ("USD", "GBP"): 0.79,
    ("USD", "JPY"): 150.50,
    ("JPY", "USD"): 0.0066,
}
CACHE_EXPIRY_SECONDS = 10


def convert_currency_tool(
    amount: float, from_currency: str, to_currency: str
) -> Dict[str, Any]:
    from_currency_upper = str(from_currency).upper().strip()
    to_currency_upper = str(to_currency).upper().strip()
    try:
        float_amount = float(amount)
    except ValueError:
        return {"error": "Invalid amount provided. Amount must be a number."}

    print(
        f"\n[TOOL EXECUTED] convert_currency_tool for {float_amount} {from_currency_upper} to {to_currency_upper}"
    )

    rate = MOCK_RATES.get((from_currency_upper, to_currency_upper))
    if rate:
        converted_amount = float_amount * rate
        return {
            "from_currency": from_currency_upper,
            "to_currency": to_currency_upper,
            "original_amount": float_amount,
            "converted_amount": converted_amount,
            "rate_used": rate,
        }
    else:
        return {
            "error": f"Conversion rate for {from_currency_upper} to {to_currency_upper} not available in mock data."
        }


def _generate_cache_key(tool_name: str, args: Dict[str, Any]) -> str:
    """Helper function to generate a consistent cache key."""
    try:
        amount_val = args.get("amount")
        if amount_val is None:
            amount_str = "0.00"
        elif isinstance(amount_val, (int, float)):
            amount_str = f"{float(amount_val):.2f}"
        else:
            try:
                amount_str = f"{float(str(amount_val)):.2f}"
            except ValueError:
                amount_str = (
                    str(amount_val).lower().strip()
                    if str(amount_val).strip()
                    else "invalid_amount_str"
                )
    except Exception:
        amount_str = "amount_key_gen_error"

    from_c_str = str(args.get("from_currency", "")).upper().strip()
    to_c_str = str(args.get("to_currency", "")).upper().strip()

    key = f"{tool_name}_{amount_str}_{from_c_str}_{to_c_str}"
    return key


async def before_tool_callback_cache(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext
) -> Optional[Dict[str, Any]]:
    tool_name = tool.name

    if tool_name == "convert_currency_tool":
        cache_key = _generate_cache_key(tool_name, args)

        # Read current cache state from tool_context.state
        # .get("app_cache", {}) ensures it defaults to an empty dict if not present
        current_app_cache = tool_context.state.get("app_cache", {})
        cached_entry = current_app_cache.get(cache_key)

        modified_app_cache = None  # To track if current_app_cache needs to be updated

        if cached_entry:
            timestamp_str, cached_result_data = cached_entry
            try:
                cached_timestamp = datetime.fromisoformat(timestamp_str)
                current_time = datetime.now(timezone.utc)
                age_seconds = (current_time - cached_timestamp).total_seconds()

                print(
                    f"[CACHE DEBUG] Found entry for {cache_key}. Age: {age_seconds:.2f}s. Expiry: {CACHE_EXPIRY_SECONDS}s."
                )

                if age_seconds < CACHE_EXPIRY_SECONDS:
                    print(
                        f"[CACHE DEBUG] Using FRESH CACHED result for {cache_key}: {cached_result_data}"
                    )
                    # Return cached result. ADK handles state persistence if modified_app_cache was set.
                    return {"cached_result": copy.deepcopy(cached_result_data)}
                else:  # Cache expired
                    print(f"[CACHE DEBUG] Cache EXPIRED for {cache_key}.")
                    # Prepare to delete the expired entry from our copy of the cache
                    modified_app_cache = current_app_cache.copy()
                    if cache_key in modified_app_cache:
                        del modified_app_cache[cache_key]
                        print(
                            f"[CACHE DEBUG] Prepared deletion of expired entry for {cache_key}."
                        )
            except ValueError:
                print(
                    f"[CACHE DEBUG] Invalid timestamp format in cache for {cache_key}. Treating as miss."
                )
                # Optionally prepare to delete the malformed entry
                modified_app_cache = current_app_cache.copy()
                if cache_key in modified_app_cache:
                    del modified_app_cache[cache_key]
                    print(
                        f"[CACHE DEBUG] Prepared deletion of malformed entry for {cache_key}."
                    )
        else:
            print(f"[CACHE DEBUG] Cache MISS for key: {cache_key}.")

        # If the app_cache was modified (e.g., an expired entry was deleted),
        # update tool_context.state. ADK framework will persist this.
        if modified_app_cache is not None and modified_app_cache != current_app_cache:
            tool_context.state["app_cache"] = modified_app_cache
            print(
                f"[BEFORE TOOL] Updated tool_context.state['app_cache'] to: {modified_app_cache}"
            )

    # If not returned cached_result, proceed with actual tool call.
    # Returning None means proceed with the tool.
    return None


async def after_tool_callback_format_and_cache(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict[str, Any],
) -> Dict[str, Any]:  # Consistently return a dict for the (modified) tool output
    tool_name = tool.name
    print(
        f"\n[AFTER TOOL] Original tool response for '{tool_name}' with args {args}: {tool_response}"
    )

    modified_tool_output: Dict[str, Any] = {}

    if tool_name == "convert_currency_tool":
        modified_tool_output["raw_details"] = copy.deepcopy(tool_response)

        if "converted_amount" in tool_response and "error" not in tool_response:
            cache_key = _generate_cache_key(tool_name, args)

            # Read current cache state from tool_context.state and prepare to update it
            current_app_cache = tool_context.state.get("app_cache", {})
            new_app_cache_to_set = current_app_cache.copy()  # Make a mutable copy

            new_app_cache_to_set[cache_key] = (
                datetime.now(timezone.utc).isoformat(),
                copy.deepcopy(tool_response),  # Cache the original tool_response
            )

            # Update tool_context.state with the new cache. ADK framework will persist this.
            tool_context.state["app_cache"] = new_app_cache_to_set
            print(
                f"[AFTER TOOL] Updated tool_context.state['app_cache'] to: {new_app_cache_to_set}"
            )

            formatted_result_string = (
                f"{tool_response['original_amount']:.2f} {tool_response['from_currency']} is "
                f"approximately {tool_response['converted_amount']:.2f} {tool_response['to_currency']} "
                f"(Rate: {tool_response.get('rate_used', 'N/A')})."
            )
            modified_tool_output["result_summary"] = formatted_result_string

        elif "error" in tool_response:
            modified_tool_output["result_summary"] = (
                f"I encountered an issue during conversion: {tool_response['error']}"
            )
            # No caching if there's an error from the tool

        print(f"[AFTER TOOL] Formatted result/error for LLM: {modified_tool_output}")

    # return original tool_response to be safe. Otherwise, return the modified version.
    if not modified_tool_output:
        return tool_response

    return modified_tool_output


currency_converter_agent = LlmAgent(
    name="currency_converter_agent",
    description="Converts currencies using mock rates, demonstrates caching and response formatting by modifying tool_context.state.",
    tools=[convert_currency_tool],
    model="gemini-2.0-flash",  # Ensure this model is appropriate / available
    instruction="You are a currency converter. When asked to convert currency, use the convert_currency_tool. Based on the tool's output (look for 'result_summary' or 'error_message' in the tool's returned dictionary), provide a clear and concise answer to the user.",
    before_tool_callback=before_tool_callback_cache,
    after_tool_callback=after_tool_callback_format_and_cache,
)

root_agent = currency_converter_agent
