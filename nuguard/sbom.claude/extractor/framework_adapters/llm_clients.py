"""LLM client library adapter.

Detects raw LLM client usage (OpenAI, Anthropic, Google, Groq, etc.) via
Python AST analysis.  Produces MODEL nodes conforming to the Xelo AI-SBOM
v1.3.0 schema.
"""

from __future__ import annotations

import ast
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

_CLIENT_TO_PROVIDER = {
    "OpenAI": "openai",
    "AsyncOpenAI": "openai",
    "Anthropic": "anthropic",
    "AsyncAnthropic": "anthropic",
    "GenerativeModel": "google",
    "ChatGoogleGenerativeAI": "google",
    "Groq": "groq",
    "AsyncGroq": "groq",
    "Ollama": "ollama",
    "MistralClient": "mistralai",
    "Together": "together",
    "Fireworks": "fireworks",
}

_BASE_URL_TO_PROVIDER = {
    "api.groq.com": "groq",
    "api.together.xyz": "together",
    "api.perplexity.ai": "perplexity",
    "api.fireworks.ai": "fireworks",
    "api.deepseek.com": "deepseek",
    "openrouter.ai": "openrouter",
    "api.cerebras.ai": "cerebras",
    "api.mistral.ai": "mistralai",
}

# Methods that indicate model usage: client.chat.completions.create, etc.
_API_CALL_METHODS = frozenset(
    {
        "chat.completions.create",
        "messages.create",
        "generate_content",
        "completions.create",
    }
)

_TRIGGER_KEYWORDS = frozenset(
    {
        "openai",
        "anthropic",
        "generativeai",
        "GenerativeModel",
        "Groq",
        "Ollama",
        "MistralClient",
        "InferenceClient",
        "boto3",
        "bedrock",
    }
)


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


class LLMClientsAdapter:
    """Extract LLM client usage from a single Python source file."""

    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]:
        """Parse *source* and return (nodes, edges)."""
        if not any(kw in source for kw in _TRIGGER_KEYWORDS):
            return [], []

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return [], []

        visitor = _LLMClientsVisitor(file_path)
        visitor.visit(tree)

        nodes: list[Node] = []
        seen_names: set[str] = set()

        for item in visitor.models:
            name = item["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            nid = _stable_id(name, NodeType.MODEL)
            nodes.append(
                Node(
                    id=nid,
                    name=name,
                    component_type=NodeType.MODEL,
                    confidence=item["confidence"],
                    metadata=NodeMetadata(
                        model_name=item.get("model_name", name),
                        framework=item.get("provider"),
                    ),
                    evidence=[
                        _evidence(
                            EvidenceKind.AST_INSTANTIATION,
                            item["confidence"],
                            item["detail"],
                            file_path,
                            item.get("line"),
                        )
                    ],
                )
            )

        return nodes, []


class _LLMClientsVisitor(ast.NodeVisitor):
    """Walk an AST and collect LLM client usage."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.models: list[dict] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._get_call_name(node)
        line = node.lineno

        # Direct client instantiation
        if func_name in _CLIENT_TO_PROVIDER:
            provider = _CLIENT_TO_PROVIDER[func_name]
            # Check for base_url override
            base_url = self._get_kwarg_str(node, "base_url")
            if base_url:
                for domain, prov in _BASE_URL_TO_PROVIDER.items():
                    if domain in base_url:
                        provider = prov
                        break
            self.models.append(
                {
                    "name": func_name,
                    "provider": provider,
                    "confidence": 0.9,
                    "detail": f"{func_name}() client instantiation (provider={provider})",
                    "line": line,
                }
            )

        # Cohere client: cohere.Client or cohere.AsyncClient
        elif func_name in ("cohere.Client", "cohere.AsyncClient"):
            self.models.append(
                {
                    "name": func_name,
                    "provider": "cohere",
                    "confidence": 0.9,
                    "detail": f"{func_name}() cohere client",
                    "line": line,
                }
            )

        # boto3.client("bedrock-runtime") → amazon provider
        elif func_name == "boto3.client":
            service = self._get_first_str_arg(node)
            if service and "bedrock" in service:
                self.models.append(
                    {
                        "name": "bedrock",
                        "provider": "amazon",
                        "confidence": 0.85,
                        "detail": f"boto3.client({service!r}) — Amazon Bedrock",
                        "line": line,
                    }
                )

        # InferenceClient(model=..., provider=...) from huggingface_hub
        elif func_name == "InferenceClient":
            model_name = self._get_kwarg_str(node, "model") or "huggingface"
            provider = self._get_kwarg_str(node, "provider") or "huggingface"
            self.models.append(
                {
                    "name": model_name,
                    "provider": provider,
                    "model_name": model_name,
                    "confidence": 0.85,
                    "detail": f"HuggingFace InferenceClient(model={model_name!r})",
                    "line": line,
                }
            )

        # OpenAI(base_url=...) with provider override
        elif func_name == "OpenAI":
            base_url = self._get_kwarg_str(node, "base_url")
            provider = "openai"
            if base_url:
                for domain, prov in _BASE_URL_TO_PROVIDER.items():
                    if domain in base_url:
                        provider = prov
                        break
            self.models.append(
                {
                    "name": f"OpenAI({provider})",
                    "provider": provider,
                    "confidence": 0.9,
                    "detail": f"OpenAI() client (provider={provider})",
                    "line": line,
                }
            )

        # API calls: client.chat.completions.create(model=...) etc.
        else:
            # Extract chained method calls like client.chat.completions.create
            method_chain = self._get_method_chain(node)
            if method_chain:
                model_name = self._get_kwarg_str(node, "model")
                if model_name:
                    self.models.append(
                        {
                            "name": model_name,
                            "provider": self._infer_provider(method_chain),
                            "model_name": model_name,
                            "confidence": 0.85,
                            "detail": f"LLM API call {method_chain}(model={model_name!r})",
                            "line": line,
                        }
                    )

        self.generic_visit(node)

    @staticmethod
    def _get_method_chain(node: ast.Call) -> str | None:
        """Extract dotted method chain from a Call node (e.g. 'chat.completions.create')."""
        parts: list[str] = []
        func = node.func
        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if parts and ".".join(reversed(parts)) in _API_CALL_METHODS:
            return ".".join(reversed(parts))
        return None

    @staticmethod
    def _infer_provider(method_chain: str) -> str:
        if "messages" in method_chain:
            return "anthropic"
        if "generate_content" in method_chain:
            return "google"
        return "openai"

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                return f"{node.func.value.id}.{node.func.attr}"
            return node.func.attr
        return ""

    @staticmethod
    def _get_kwarg_str(node: ast.Call, key: str) -> str | None:
        for kw in node.keywords:
            if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return None

    @staticmethod
    def _get_first_str_arg(node: ast.Call) -> str | None:
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        return None
