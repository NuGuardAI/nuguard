"""
CipherBank Orchestrator Middleware
====================================
SBOM COMPLEXITY TEST #5 (part B) — Re-export surface
-----------------------------------------------------
The SBOM scanner sees only these imports when scanning __init__.py:
  ``from ._llm_gate import LLMGuard``   ← relative import, not an LLM package prefix

``._llm_gate`` does NOT match any adapter's ``handles_imports`` list:
  handles_imports = ["langchain_openai", "langchain_core", ...]

So the import line here is transparent — no adapter fires.
The LLMGuard class is re-exported as a public symbol but its origin
(an AzureChatOpenAI-wrapping class) is invisible from this file alone.
"""
from ._llm_gate import LLMGuard

__all__ = ["LLMGuard"]
