"""LLM provider factory — uses OpenAI SDK as a universal proxy.

Each provider (Groq, Gemini, Ollama) is accessed via a custom base_url so
that the same asyncio-compatible client interface is reused throughout.
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI

# ── Groq ──────────────────────────────────────────────────────────────────
_groq_client = AsyncOpenAI(
    api_key=os.environ.get("GROQ_API_KEY", ""),
    base_url="https://api.groq.com/openai/v1",
)

# ── Google Gemini via OpenAI-compatible endpoint ───────────────────────────
_gemini_client = AsyncOpenAI(
    api_key=os.environ.get("GEMINI_API_KEY", ""),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)

# ── Ollama (local) ─────────────────────────────────────────────────────────
_ollama_client = AsyncOpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1",
)


async def chat(provider: str, model: str, messages: list[dict]) -> str:
    """Route a chat completion request to the correct provider."""
    clients = {
        "groq": (_groq_client, "llama-3.3-70b-versatile"),
        "gemini": (_gemini_client, "gemini-2.0-flash"),
        "ollama": (_ollama_client, "llama3.2:3b"),
    }
    client, default_model = clients[provider]
    chosen_model = model or default_model
    resp = await client.chat.completions.create(
        model=chosen_model,
        messages=messages,
        temperature=0.0,
    )
    return resp.choices[0].message.content or ""
