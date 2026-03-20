"""Configuration for the AI-SBOM extractor pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AiSbomConfig:
    """Controls how the :class:`~nuguard.sbom.extractor.core.AiSbomExtractor`
    scans a source tree.

    Attributes:
        enable_llm: When ``True`` and ``LITELLM_API_KEY`` is set, the extractor
            uses the LLM to enrich nodes with higher-fidelity descriptions.
        llm_model: LiteLLM model string used for enrichment.
        llm_budget_tokens: Approximate token budget for LLM enrichment calls.
        include_deps: Scan ``pyproject.toml``, ``requirements.txt``, and
            ``package.json`` for package dependencies.
        include_iac: Scan IaC files (Terraform, Docker Compose, Kubernetes
            manifests, GitHub Actions workflows).
        min_confidence: Drop nodes whose computed confidence is below this
            threshold.
        exclude_patterns: ``fnmatch``-style glob patterns.  Files matching any
            of these patterns are skipped.
    """

    enable_llm: bool = False
    llm_model: str = "gemini/gemini-2.0-flash"
    llm_budget_tokens: int = 50_000
    include_deps: bool = True
    include_iac: bool = True
    min_confidence: float = 0.3
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            "**/test*",
            "**/.venv/**",
            "**/node_modules/**",
            "**/dist/**",
        ]
    )
