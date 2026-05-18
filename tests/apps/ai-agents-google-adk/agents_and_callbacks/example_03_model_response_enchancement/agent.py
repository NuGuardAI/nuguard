import copy  # For deepcopy
import re
from typing import Any, Dict, Optional

from google.adk.agents import Agent as LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.genai import types


def after_model_callback_enhance(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    if not llm_response.content or not llm_response.content.parts:
        print("\n[AFTER MODEL] LLM response is empty or malformed. No modifications.")
        return llm_response

    # Deepcopy to avoid modifying the original response object directly if it's immutable
    # or if other callbacks might need the original.
    modified_llm_response = copy.deepcopy(llm_response)

    # Assuming the main text is in the first part
    original_text = modified_llm_response.content.parts[0].text
    current_text = original_text  # Start with original for modification

    print(f"\n[AFTER MODEL] Original LLM response: '{original_text}'")

    # 1. Attempt to extract mock flight details
    # Example: "Okay, I've booked flight BA245 from London to Paris on 2025-12-25 for you."
    flight_match = re.search(
        r"flight\s+(?P<flight_number>[A-Z0-9]{2,6})\s+from\s+(?P<origin>[\w\s]+?)\s+to\s+(?P<destination>[\w\s]+?)\s+on\s+(?P<date>\d{4}-\d{2}-\d{2})",
        original_text,
        re.IGNORECASE,
    )
    if flight_match:
        flight_details = flight_match.groupdict()  # Gets a dict with named groups
        flight_details["origin"] = flight_details["origin"].strip()
        flight_details["destination"] = flight_details["destination"].strip()

        callback_context.state["extracted_flight_info"] = flight_details
        # For ADK web, artifacts are usually added differently, often by the tool itself or agent logic.
        # Storing in state is a simple way to demonstrate data extraction.
        # To show it in UI, you might need to modify the response text or use ADK's specific artifact features.
        print(f"[AFTER MODEL] Extracted flight info: {flight_details}")
        current_text += f"\n\n**Flight Summary Logged:**\nNumber: {flight_details['flight_number']}\nFrom: {flight_details['origin']}\nTo: {flight_details['destination']}\nDate: {flight_details['date']}"

    # 2. Add quick links for common topics
    if "refund polic" in original_text.lower():
        refund_link = (
            "\nFor more details, see our [Refund Policy](https://example.com/refunds)."
        )
        if refund_link not in current_text:  # Avoid adding duplicate links
            current_text += refund_link
            print("[AFTER MODEL] Added refund policy link.")

    if "baggage allowance" in original_text.lower():
        baggage_link = "\nCheck our [Baggage Allowance](https://example.com/baggage)."
        if baggage_link not in current_text:
            current_text += baggage_link
            print("[AFTER MODEL] Added baggage allowance link.")

    if current_text != original_text:
        modified_llm_response.content.parts[0].text = current_text
        print(f"[AFTER MODEL] Enhanced response being sent to user: '{current_text}'")
    else:
        print("[AFTER MODEL] No enhancements made to the LLM response text.")

    print("\n")
    return modified_llm_response


travel_response_enhancer_agent = LlmAgent(
    name="travel_response_enhancer_agent",
    description="A travel assistant that enhances LLM responses with structured data extraction and quick links.",
    model="gemini-2.0-flash",
    instruction="""You are a helpful travel assistant.
If a user asks to book a flight, confirm the booking with made-up details including a flight number (e.g., BA245, AF1800), origin city, destination city, and date (YYYY-MM-DD format).
If a user asks about policies, provide a general answer.
Example of flight booking confirmation: "Okay, I've booked flight BA245 from London to Paris on 2025-12-25 for you."
""",
    after_model_callback=after_model_callback_enhance,
)

root_agent = travel_response_enhancer_agent
