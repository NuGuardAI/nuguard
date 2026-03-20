"""TypeScript LLM clients adapter.

Detects raw LLM client usage in TypeScript/JavaScript files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from nuguard.models.sbom import (
    Edge,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)
from nuguard.sbom.extractor.ts_parser import TSParseResult

_TRIGGER_MODULES = frozenset(
    {
        "openai",
        "@anthropic-ai/sdk",
        "@google/generative-ai",
        "@google-cloud/vertexai",
        "groq",
        "ollama",
        "@mistralai/mistralai",
        "cohere-ai",
        "together-ai",
        "@huggingface/inference",
    }
)

_TS_CLIENT_TO_PROVIDER = {
    "OpenAI": "openai",
    "Anthropic": "anthropic",
    "GoogleGenerativeAI": "google",
    "VertexAI": "google",
    "Groq": "groq",
    "Ollama": "ollama",
    "MistralClient": "mistralai",
    "CohereClient": "cohere",
    "Together": "together",
    "HfInference": "huggingface",
}

_TS_BASE_URL_TO_PROVIDER = {
    "api.groq.com": "groq",
    "api.together.xyz": "together",
    "api.perplexity.ai": "perplexity",
    "api.fireworks.ai": "fireworks",
    "api.deepseek.com": "deepseek",
    "openrouter.ai": "openrouter",
    "api.mistral.ai": "mistralai",
}


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _evidence(
    kind: EvidenceKind,
    confidence: float,
    detail: str,
    path: Path,
    line: int | None = None,
) -> Evidence:
    return Evidence(
        kind=kind,
        confidence=confidence,
        detail=detail,
        location=EvidenceLocation(path=str(path), line=line),
    )


class LLMClientsTSAdapter:
    """Extract LLM client usage from a parsed TypeScript/JavaScript file."""

    TRIGGER_MODULES = _TRIGGER_MODULES

    def can_handle(self, result: TSParseResult) -> bool:
        return bool(result.has_module & self.TRIGGER_MODULES)

    def extract(self, file_path: Path, result: TSParseResult) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()

        for call in result.calls:
            name = call.name.replace("new ", "")
            line = call.line

            if name in _TS_CLIENT_TO_PROVIDER:
                provider = _TS_CLIENT_TO_PROVIDER[name]
                # Check for baseURL kwarg override
                base_url = call.kwargs.get("baseURL") or call.kwargs.get("base_url") or ""
                if base_url:
                    for domain, prov in _TS_BASE_URL_TO_PROVIDER.items():
                        if domain in base_url:
                            provider = prov
                            break
                node_name = f"{name}({provider})" if provider != "openai" or name != "OpenAI" else name
                if node_name in seen:
                    continue
                seen.add(node_name)
                nid = _stable_id(node_name, NodeType.MODEL)
                nodes.append(
                    Node(
                        id=nid,
                        name=node_name,
                        component_type=NodeType.MODEL,
                        confidence=0.9,
                        metadata=NodeMetadata(
                            framework=provider,
                        ),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST_INSTANTIATION,
                                0.9,
                                f"TS LLM client new {name}() (provider={provider})",
                                file_path,
                                line,
                            )
                        ],
                    )
                )

            # Detect model= in API call kwargs
            elif call.kwargs.get("model"):
                model_name = call.kwargs["model"]
                if model_name in seen:
                    continue
                seen.add(model_name)
                nid = _stable_id(model_name, NodeType.MODEL)
                nodes.append(
                    Node(
                        id=nid,
                        name=model_name,
                        component_type=NodeType.MODEL,
                        confidence=0.85,
                        metadata=NodeMetadata(model_name=model_name),
                        evidence=[
                            _evidence(
                                EvidenceKind.AST,
                                0.85,
                                f"TS LLM API call with model={model_name!r}",
                                file_path,
                                line,
                            )
                        ],
                    )
                )

        return nodes, []
