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

from xelo.adapters.typescript.agno import AgnoTSAdapter
from xelo.adapters.typescript.azure_ai_agents import AzureAIAgentsTSAdapter
from xelo.adapters.typescript.bedrock_agents import BedrockAgentsTSAdapter
from xelo.adapters.typescript.datastores import DatastoreTSAdapter
from xelo.adapters.typescript.google_adk import GoogleADKAdapter
from xelo.adapters.typescript.langgraph import LangGraphTSAdapter
from xelo.adapters.typescript.llm_clients import LLMClientTSAdapter
from xelo.adapters.typescript.openai_agents import OpenAIAgentsTSAdapter
from xelo.adapters.typescript.prompts import PromptTSAdapter

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
