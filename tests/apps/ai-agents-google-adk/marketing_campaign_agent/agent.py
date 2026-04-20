import os

try:
    from dotenv import load_dotenv
    load_dotenv()

    MODEL_NAME = os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.0-flash")
except ImportError:
    print("Warning: python-dotenv not installed. Ensure API key is set")
    MODEL_NAME = "gemini-2.0-flash"

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools import google_search

from marketing_campaign_agent.instructions import (
    MARKET_RESEARCH_INSTRUCTION,
    MESSAGING_STRATEGIST_INSTRUCTION,
    AD_COPY_WRITER_INSTRUCTION,
    VISUAL_SUGGESTER_INSTRUCTION,
    FORMATTER_INSTRUCTION,
    CAMPAIGN_ORCHESTRATOR_INSTRUCTION
)

market_research_agent = LlmAgent(
    name="MarketResearcher",
    model=MODEL_NAME,
    instruction=MARKET_RESEARCH_INSTRUCTION,
    tools=[google_search],
    output_key="market_research_summary"
)

# --- Sub Agent 2: MessagingStrategist ---
messaging_strategist_agent = LlmAgent(
    name="MessagingStrategist",
    model=MODEL_NAME,  # Using environment variable
    instruction=MESSAGING_STRATEGIST_INSTRUCTION,
    # This agent will automatically receive the output of the previous agent (MarketResearcher)
    # and can also access other state variables if needed, e.g.,
    # instruction="Craft core messaging based on market insights: {{state.market_research_summary}}"
    output_key="key_messaging" # Save result to state under this key
)

# --- Sub Agent 3: AdCopyWriter ---
ad_copy_writer_agent = LlmAgent(
    name="AdCopyWriter",
    model=MODEL_NAME,  # Using environment variable
    instruction=AD_COPY_WRITER_INSTRUCTION,
    # instruction="Write ad copy variations based on key messages: {{state.key_messaging}}"
    output_key="ad_copy_variations" # Save result to state
)

# --- Sub Agent 4: VisualSuggester ---
visual_suggester_agent = LlmAgent(
    name="VisualSuggester",
    model=MODEL_NAME,  # Using environment variable
    instruction=VISUAL_SUGGESTER_INSTRUCTION,
     # instruction="Suggest visual concepts for ad copy: {{state.ad_copy_variations}}"
    output_key="visual_concepts" # Save result to state
)

# --- Sub Agent 5: Formatter ---
# This agent will read multiple state keys and combine into the final Markdown
formatter_agent = LlmAgent(
    name="CampaignBriefFormatter",
    model=MODEL_NAME,  # Using environment variable
    instruction=FORMATTER_INSTRUCTION,
    output_key="final_campaign_brief" # Save final result to state
)

campaign_orchestrator = SequentialAgent(
    name="MarketingCampaignAssistant",
    description=CAMPAIGN_ORCHESTRATOR_INSTRUCTION,
    sub_agents=[
        market_research_agent,
        messaging_strategist_agent,
        ad_copy_writer_agent,
        visual_suggester_agent,
        formatter_agent,
    ]
)

root_agent = campaign_orchestrator
