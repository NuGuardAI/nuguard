"""LLM client detection adapter.

Detects direct SDK client instantiations across all major AI providers:
- OpenAI: ``OpenAI()``, ``AsyncOpenAI()``, ``AzureOpenAI()``
- Anthropic: ``Anthropic()``, ``AsyncAnthropic()``
- Google: ``GenerativeModel()``, ``vertexai``
- Mistral, Cohere, Groq, Ollama, Bedrock
- API call patterns: ``client.chat.completions.create(model="...")``
- Proxy pattern: ``OpenAI(base_url="https://api.groq.com/...")`` → resolves to Groq
"""

from __future__ import annotations

import logging
import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter
from xelo.adapters.models_kb import (
    ALL_LLM_CLASSES,
    LANGCHAIN_LLM_CLASS_PROVIDERS,
    LLM_CLIENT_PATTERNS,
    get_model_details,
    infer_provider,
)
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_log = logging.getLogger(__name__)

# API call patterns that specify a model
_MODEL_SPECIFYING_METHODS = re.compile(
    r"\b(chat\.completions\.create|completions\.create|messages\.create|generate_content"
    r"|invoke_model|invoke_model_with_response_stream|converse|converse_stream"
    r"|from_pretrained)\b"
)

# Classes that are treated as model-specifying (even without explicit model arg)
_MODEL_SPECIFYING_CLASSES = {"GenerativeModel", "ChatModel", "TextGenerationModel"}

# ---------------------------------------------------------------------------
# base_url → provider resolution for OpenAI-compatible proxy pattern
# ---------------------------------------------------------------------------
# Many agentic apps use a single OpenAI SDK client with a custom base_url
# pointing at Groq, Gemini, Ollama, or other compatible providers.  Detecting
# the base_url allows correct provider attribution even without provider-SDK imports.

_BASE_URL_TO_PROVIDER: list[tuple[str, str]] = [
    # Matched in order — more specific substrings first
    ("api.groq.com", "groq"),
    ("generativelanguage.googleapis.com", "google"),
    ("aiplatform.googleapis.com", "google"),
    ("localhost:11434", "ollama"),
    ("127.0.0.1:11434", "ollama"),
    ("0.0.0.0:11434", "ollama"),
    ("api.anthropic.com", "anthropic"),
    ("api.mistral.ai", "mistral"),
    ("api.together.xyz", "togetherai"),
    ("api.deepseek.com", "deepseek"),
    ("openrouter.ai", "openrouter"),
    ("api.cohere.ai", "cohere"),
    ("inference.cerebras.ai", "cerebras"),
    ("api.fireworks.ai", "fireworks"),
    ("api.perplexity.ai", "perplexity"),
]


def _resolve_provider_from_base_url(base_url: str) -> str | None:
    """Resolve a provider string from an OpenAI-compatible ``base_url`` value.

    Returns the provider name (e.g. ``"groq"``) or ``None`` if the URL doesn't
    match any known provider.
    """
    if not base_url:
        return None
    url_lower = base_url.lower()
    for substring, provider in _BASE_URL_TO_PROVIDER:
        if substring in url_lower:
            _log.debug("base_url %r → provider %r", base_url, provider)
            return provider
    return None


class LLMClientsAdapter(FrameworkAdapter):
    """Detect standalone LLM client usage across all major providers."""

    name = "llm_clients"
    priority = 90
    handles_imports = [
        "openai",
        "anthropic",
        "google.generativeai",
        "google.genai",
        "vertexai",
        "mistralai",
        "cohere",
        "groq",
        "ollama",
        "boto3",
    ]

    def extract(self, content: str, file_path: str, parse_result: Any) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        detected: list[ComponentDetection] = [self._framework_node(file_path)]
        detected_providers: set[str] = set()

        # Build a constant map from module-level string assignments for variable resolution
        # e.g. MODEL_ID_FLASH = "gemini-2.0-flash" → {"MODEL_ID_FLASH": "gemini-2.0-flash"}
        const_map: dict[str, str] = {
            lit.context: lit.value
            for lit in parse_result.string_literals
            if lit.context and not lit.is_docstring and lit.value
        }

        def _resolve_model_val(raw: Any) -> str:
            """Resolve a model value, following variable references via const_map."""
            if not isinstance(raw, str):
                return ""  # ignore lists, None, etc.
            if raw.startswith("$"):
                raw = const_map.get(raw[1:], "")
            return self._clean_str(raw)

        # Determine which providers are imported
        for imp in parse_result.imports:
            module = imp.module or ""
            for provider, cfg in LLM_CLIENT_PATTERNS.items():
                if any(module == pat or module.startswith(pat + ".") for pat in cfg["imports"]):
                    detected_providers.add(provider)

        # Extract class instantiations
        for inst in parse_result.instantiations:
            # Direct SDK classes (OpenAI, Anthropic, etc.)
            if inst.class_name in ALL_LLM_CLASSES:
                provider = self._resolve_provider(inst.class_name, detected_providers, parse_result)
                is_azure = "Azure" in inst.class_name
                args = inst.args or {}

                # Resolve provider from base_url for OpenAI-compatible proxy pattern.
                # e.g. OpenAI(base_url="https://api.groq.com/openai/v1") → groq
                base_url_provider: str | None = None
                if inst.class_name in {"OpenAI", "AsyncOpenAI"}:
                    raw_url = self._clean_str(args.get("base_url"))
                    if raw_url:
                        base_url_provider = _resolve_provider_from_base_url(raw_url)
                        if base_url_provider:
                            provider = base_url_provider
                            _log.debug(
                                "%s: OpenAI proxy → provider=%r (base_url=%r)",
                                file_path,
                                base_url_provider,
                                raw_url,
                            )

                model_name = _resolve_model_val(
                    args.get("model")
                    or args.get("model_name")
                    or args.get("model_id")  # LangChain Bedrock uses model_id=
                    or args.get("embedding_model")
                )
                # For model-specifying classes (e.g. GenerativeModel), fall back to first
                # positional arg (e.g. GenerativeModel("gemini-2.0-flash"))
                if not model_name and inst.class_name in _MODEL_SPECIFYING_CLASSES:
                    for pa in inst.positional_args:
                        model_name = _resolve_model_val(pa)
                        if model_name:
                            break

                # Skip bare client objects without an explicit model,
                # UNLESS the base_url resolved to a known provider — in that case
                # emit a FRAMEWORK node so the proxied provider is visible in the SBOM.
                if not model_name and inst.class_name not in _MODEL_SPECIFYING_CLASSES:
                    if base_url_provider:
                        raw_url = self._clean_str(args.get("base_url"))
                        detected.append(
                            ComponentDetection(
                                component_type=ComponentType.FRAMEWORK,
                                canonical_name=f"framework:{base_url_provider}",
                                display_name=base_url_provider,
                                adapter_name=self.name,
                                priority=self.priority,
                                confidence=0.88,
                                metadata={
                                    "framework": base_url_provider,
                                    "via_openai_proxy": True,
                                    "base_url": raw_url,
                                },
                                file_path=file_path,
                                line=inst.line,
                                snippet=f"{inst.class_name}(base_url={raw_url!r})",
                                evidence_kind="ast_instantiation",
                            )
                        )
                    continue

                display = model_name or f"{provider}_client"
                details = get_model_details(display, "azure" if is_azure else provider, args)

                meta: dict[str, Any] = {
                    "framework": base_url_provider or ("azure_openai" if is_azure else provider),
                    "client_class": inst.class_name,
                    "provider": "azure" if is_azure else provider,
                    "is_async": inst.class_name.startswith("Async"),
                    **{k: v for k, v in details.items() if v is not None},
                }
                if is_azure:
                    depl = self._clean_str(
                        args.get("azure_deployment") or args.get("deployment_name")
                    )
                    if depl:
                        meta["deployment_name"] = depl

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=canonicalize_text(display.lower()),
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata=meta,
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                    )
                )

            # LangChain wrappers (ChatOpenAI, ChatAnthropic, etc.)
            elif inst.class_name in LANGCHAIN_LLM_CLASS_PROVIDERS:
                provider = LANGCHAIN_LLM_CLASS_PROVIDERS[inst.class_name]
                args = inst.args or {}
                model_name = (
                    self._clean_str(
                        args.get("model")
                        or args.get("model_name")
                        or args.get("embedding_model")
                        or args.get("deployment_name")
                    )
                    or inst.class_name
                )
                details = get_model_details(model_name, provider, args)

                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=canonicalize_text(model_name.lower()),
                        display_name=model_name,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": provider,
                            "class_name": inst.class_name,
                            "provider": provider,
                            **{k: v for k, v in details.items() if v is not None},
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=f"{inst.class_name}(...)",
                        evidence_kind="ast_instantiation",
                    )
                )

        # Extract API call patterns (client.chat.completions.create(model="gpt-4o"))
        for call in parse_result.function_calls:
            func = call.function_name or ""
            args = call.args or {}

            # Determine call strength:
            # - Strong: known LLM API method names → allow positional-arg fallback
            # - Weak: generic create/generate → require explicit model= kwarg only
            is_ollama = (call.receiver or "").lower() == "ollama"
            is_strong_call = bool(_MODEL_SPECIFYING_METHODS.search(func)) or is_ollama
            is_weak_call = not is_strong_call and ("create" in func or "generate" in func)
            if not is_strong_call and not is_weak_call:
                continue

            model_name = _resolve_model_val(
                args.get("model")
                or args.get("model_name")
                or args.get("modelId")  # boto3 Bedrock invoke_model uses modelId=
                or args.get("model_id")  # some SDKs use model_id=
            )
            if not model_name and is_strong_call:
                # Only fall back to positional args for well-known LLM API calls
                # to avoid false positives from unrelated generate_*/create_* functions
                for pa in call.positional_args:
                    model_name = _resolve_model_val(pa)
                    if model_name:
                        break
            if not model_name:
                continue

            provider = (
                "ollama"
                if (call.receiver or "").lower() == "ollama"
                else infer_provider(model_name)
            )
            if provider == "unknown" and detected_providers:
                provider = sorted(detected_providers)[0]

            details = get_model_details(model_name, provider, {})

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=canonicalize_text(model_name.lower()),
                    display_name=model_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.95,
                    metadata={
                        "framework": provider,
                        "source": "api_call",
                        "api_method": func,
                        "provider": provider,
                        **{k: v for k, v in details.items() if v is not None},
                    },
                    file_path=file_path,
                    line=call.line,
                    snippet=f"{func}(model={model_name!r})",
                    evidence_kind="ast_call",
                )
            )

        # GCS storage bucket → DATASTORE
        # e.g. `bucket = storage_client.bucket(BUCKET_NAME)` where BUCKET_NAME = "mlops-for-genai"
        gcs_imported = any(
            (imp.module or "").startswith("google.cloud") for imp in parse_result.imports
        )
        if gcs_imported:
            const_map = {
                lit.context: lit.value
                for lit in parse_result.string_literals
                if lit.context and not lit.is_docstring and lit.value
            }
            for call in parse_result.function_calls:
                if call.function_name != "bucket":
                    continue
                raw: str | None = None
                if call.positional_args:
                    first = call.positional_args[0]
                    if isinstance(first, str) and not first.startswith("$"):
                        raw = first.strip("'\"")
                    elif isinstance(first, str) and first.startswith("$"):
                        raw = const_map.get(first[1:])
                bucket_name = raw or self._clean_str(
                    call.args.get("bucket_name") or call.args.get("bucket")
                )
                if bucket_name:
                    canon = canonicalize_text(f"gcs:datastore:{bucket_name}")
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.DATASTORE,
                            canonical_name=canon,
                            display_name=bucket_name,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.85,
                            metadata={
                                "datastore_type": "object_storage",
                                "provider": "gcs",
                            },
                            file_path=file_path,
                            line=call.line,
                            snippet=f"storage.bucket({bucket_name!r})",
                            evidence_kind="ast_call",
                        )
                    )

        return detected

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_str(value: Any) -> str:
        if value is None:
            return ""
        s = str(value).strip("'\"` ")
        if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
            return ""
        return s

    @staticmethod
    def _resolve_provider(class_name: str, detected: set[str], parse_result: Any) -> str:
        from xelo.adapters.models_kb import _CLASS_TO_PROVIDERS

        candidates = _CLASS_TO_PROVIDERS.get(class_name, [])
        if not candidates:
            return "unknown"
        if len(candidates) == 1:
            return candidates[0]
        # Use import context to narrow down
        imported = {imp.module for imp in parse_result.imports}
        for cand in candidates:
            patterns = LLM_CLIENT_PATTERNS.get(cand, {}).get("imports", [])
            if any(any(imp == p or imp.startswith(p + ".") for p in patterns) for imp in imported):
                return cand
        for cand in candidates:
            if cand in detected:
                return cand
        return candidates[0]
