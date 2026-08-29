"""Python-specific framework adapters for NuGuard SBOM extraction."""

from .agno import AgnoAdapter
from .ai_security_vendors import AISecurityVendorsAdapter
from .autogen import AutoGenAdapter
from .aws_bedrock_guardrails import AWSBedrockGuardrailsAdapter
from .azure_ai_agents import AzureAIAgentsAdapter
from .azure_content_safety import AzureContentSafetyAdapter
from .bedrock_agentcore import BedrockAgentCoreAdapter
from .claude_agent_sdk import ClaudeAgentSDKAdapter
from .crewai import CrewAIAdapter
from .datastores import PythonDatastoreAdapter
from .fastapi_adapter import FastAPIAdapter
from .flask_adapter import FlaskAdapter
from .gcp_model_armor import GCPModelArmorAdapter
from .google_adk import GoogleADKPythonAdapter
from .guardrail_heuristic import GuardrailHeuristicAdapter
from .guardrails_ai import GuardrailsAIAdapter
from .langchain_moderation import LangChainModerationAdapter
from .langgraph import LangGraphAdapter
from .llamaindex import LlamaIndexAdapter
from .llm_clients import LLMClientsAdapter
from .mcp_client import MCPClientAdapter
from .mcp_server import MCPServerAdapter
from .openai_agents import OpenAIAgentsAdapter
from .openai_function_schema import OpenAIFunctionSchemaAdapter
from .semantic_kernel import SemanticKernelAdapter

__all__ = [
    "AgnoAdapter",
    "AISecurityVendorsAdapter",
    "AutoGenAdapter",
    "AWSBedrockGuardrailsAdapter",
    "AzureAIAgentsAdapter",
    "AzureContentSafetyAdapter",
    "BedrockAgentCoreAdapter",
    "ClaudeAgentSDKAdapter",
    "CrewAIAdapter",
    "PythonDatastoreAdapter",
    "FastAPIAdapter",
    "FlaskAdapter",
    "GCPModelArmorAdapter",
    "GoogleADKPythonAdapter",
    "GuardrailHeuristicAdapter",
    "GuardrailsAIAdapter",
    "LangChainModerationAdapter",
    "LangGraphAdapter",
    "LlamaIndexAdapter",
    "LLMClientsAdapter",
    "MCPClientAdapter",
    "MCPServerAdapter",
    "OpenAIAgentsAdapter",
    "OpenAIFunctionSchemaAdapter",
    "SemanticKernelAdapter",
]
