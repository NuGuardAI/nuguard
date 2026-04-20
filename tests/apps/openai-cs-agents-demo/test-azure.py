"""Smoke test hitting Azure OpenAI via nuguard's shared LLMClient."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from nuguard.common.llm_client import LLMClient

load_dotenv(Path(__file__).parent / ".env")

ENDPOINT = os.environ.get(
    "AZURE_REDTEAM_LLM_ENDPOINT",
    "https://ng-ai-foundary.openai.azure.com/openai/v1",
)
DEPLOYMENT = os.environ.get("AZURE_REDTEAM_LLM_DEPLOYMENT", "gpt-4.1-mini")
API_KEY = os.environ.get("AZURE_REDTEAM_LLM_KEY", None)


async def main() -> None:
    print(f"Testing Azure OpenAI endpoint {ENDPOINT} with deployment {DEPLOYMENT}... and API key starting with {API_KEY if API_KEY else None}")
    # Azure AI Foundry exposes an OpenAI-compatible v1 API, so we route
    # through LiteLLM's openai/ provider with a custom api_base instead of
    # its azure/ provider (which expects the classic .openai.azure.com shape).
    client = LLMClient(
        model=f"openai/{DEPLOYMENT}",
        api_key=API_KEY,
        api_base=ENDPOINT,
    )
    reply = await client.complete(
        "What is the capital of France?",
        label="test-azure",
    )
    print(reply)


if __name__ == "__main__":
    asyncio.run(main())
