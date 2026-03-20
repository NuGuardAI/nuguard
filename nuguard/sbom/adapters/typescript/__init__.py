"""TypeScript/JavaScript Framework Adapters for Xelo SBOM.

Supports detection of AI frameworks in TypeScript and JavaScript code:
- LangGraph.js / LangChain.js
- OpenAI Agents SDK
- Google ADK (Genkit)
- Common LLM clients (OpenAI, Anthropic, Google AI, Cohere, Mistral, Groq)
- Prompt detection and analysis
- Datastore detection (SQL, Vector DBs, Object Storage)
- AWS Bedrock Agents
- Agno (via @ag-ui/agno client package)
- Azure AI Agent Service (@azure/ai-agents, @azure/ai-projects)
"""

from .agno import AgnoTSAdapter
from .azure_ai_agents import AzureAIAgentsTSAdapter
from .bedrock_agents import BedrockAgentsTSAdapter
from .datastores import DatastoreTSAdapter
from .google_adk import GoogleADKAdapter
from .langgraph import LangGraphTSAdapter
from .llm_clients import LLMClientTSAdapter
from .openai_agents import OpenAIAgentsTSAdapter
from .prompts import PromptTSAdapter

__all__ = [
    "AgnoTSAdapter",
    "AzureAIAgentsTSAdapter",
    "BedrockAgentsTSAdapter",
    "DatastoreTSAdapter",
    "GoogleADKAdapter",
    "LangGraphTSAdapter",
    "LLMClientTSAdapter",
    "OpenAIAgentsTSAdapter",
    "PromptTSAdapter",
]
