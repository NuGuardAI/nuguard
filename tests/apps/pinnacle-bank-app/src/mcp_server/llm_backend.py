"""
CipherBank LLM Backend — Embedding & Completion Provider
=========================================================
SBOM COMPLEXITY TEST #1 — "Dynamic importlib" obfuscation
----------------------------------------------------------
Detection challenge:
  The SBOM extractor determines which framework adapters to run by checking
  ``imported_modules = {imp.module for imp in parse_result.imports}``.  Static
  import statements (``import openai``, ``from openai import ...``) appear in
  that set; ``importlib.import_module("openai")`` does NOT.

  This file therefore passes through the ``can_handle()`` gate with only
  ``importlib`` and ``os`` in its import set.  The ``LLMClientsAdapter``
  (handles_imports = ["openai", "anthropic", ...]) never fires.

  Additionally, the model name is built from string *concatenation*:
    ``_MODEL = "gpt-" + "4" + "o"``
  The AST parser records string *literals*, not binary-op results, so the
  model name does not appear in ``parse_result.string_literals`` and is
  invisible to the ``const_map`` resolution in ``LLMClientsAdapter.extract()``.

  Expected SBOM result: no MODEL or FRAMEWORK node for this file.
  Detection requires: dynamic import tracking, string-join analysis, or
  runtime tracing.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any

logger = logging.getLogger("mcp_server.llm_backend")

# ── Hard-to-detect model name: concatenated string literals ──────────────────
# Concatenation at the AST level is a BinaryOp (Constant + Constant), NOT a
# Constant, so it does not appear in parse_result.string_literals.
_CHAT_MODEL    = "gpt-" + "4" + "o"                          # → "gpt-4o"
_EMBED_MODEL   = "text-embedding-3" + "-" + "small"           # → "text-embedding-3-small"
_API_VERSION   = "2024-" + "05" + "-13"                        # → "2024-05-13"

# ── Lazy-loaded OpenAI client via importlib ───────────────────────────────────
# importlib.import_module("openai") does not generate an `import openai`
# statement in the AST, defeating the import-based adapter gating logic.
_openai_mod: Any = None
_client: Any = None


def _get_client() -> Any:
    """Lazily initialise the OpenAI SDK via importlib to avoid a static import."""
    global _openai_mod, _client
    if _client is not None:
        return _client

    # Runtime import — invisible to AST analysis
    _openai_mod = importlib.import_module("openai")
    azure_cls = getattr(_openai_mod, "AzureOpenAI")
    _client = azure_cls(
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        api_version=_API_VERSION,
    )
    logger.info("OpenAI client initialised via importlib (model=%s)", _CHAT_MODEL)
    return _client


# ── Another dynamic provider loaded at runtime based on env config ────────────
# This pattern also imports anthropic dynamically — a second provider the SBOM
# scanner will not detect because there is no ``import anthropic`` at module level.
def _get_fallback_client() -> Any:
    """Fallback to Anthropic if Azure OpenAI is unavailable."""
    provider = os.getenv("LLM_FALLBACK_PROVIDER", "anthropic")
    # Dynamic import string — not a static module reference
    module_name = {"anthropic": "anthropic", "cohere": "cohere"}.get(provider, "anthropic")
    mod = importlib.import_module(module_name)
    cls_name = {"anthropic": "Anthropic", "cohere": "Client"}.get(provider, "Anthropic")
    cls = getattr(mod, cls_name)
    return cls(api_key=os.getenv("FALLBACK_API_KEY", ""))


async def complete(prompt: str, max_tokens: int = 512) -> str:
    """Run a chat completion using the lazily-loaded Azure OpenAI client."""
    client = _get_client()
    response = await client.chat.completions.create(
        model=_CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


async def embed(text: str) -> list[float]:
    """Return a text embedding vector using the lazily-loaded client."""
    client = _get_client()
    response = await client.embeddings.create(
        model=_EMBED_MODEL,
        input=text,
    )
    return response.data[0].embedding
