"""nuguard configuration.

Environment variables
---------------------
NUGUARD_LLM                  Set to "1" / "true" to enable LLM enrichment
NUGUARD_LLM_MODEL            LLM model string passed to litellm (default: gpt-4o-mini)
NUGUARD_LLM_API_KEY          API key for the LLM provider
NUGUARD_LLM_API_BASE         Base URL for the LLM provider
NUGUARD_LLM_BUDGET_TOKENS    Max tokens to spend on LLM enrichment (default: 500000)
NUGUARD_GAP_FILL_MAX_CALLS   Max LLM calls for the gap-fill discovery pass (default: 40)
NUGUARD_GAP_FILL_MAX_COST_USD  Max estimated USD spend for gap-fill (default: 5.0)
NUGUARD_GAP_FILL_ENABLE_PRIVILEGE  Opt into PRIVILEGE gap-fill (default: false)
NUGUARD_GAP_FILL_ENABLE_GUARDRAIL  Opt into GUARDRAIL gap-fill (default: false)

Legacy aliases (still accepted for backwards compatibility)
-----------------------------------------------------------
AISBOM_ENABLE_LLM         → NUGUARD_LLM
AISBOM_LLM_MODEL          → NUGUARD_LLM_MODEL
AISBOM_LLM_API_KEY        → NUGUARD_LLM_API_KEY
AISBOM_LLM_API_BASE       → NUGUARD_LLM_API_BASE
AISBOM_LLM_BUDGET_TOKENS  → NUGUARD_LLM_BUDGET_TOKENS
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field, model_validator


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get(primary: str, *aliases: str) -> str | None:
    """Return the first non-empty value from *primary* then *aliases*."""
    for key in (primary, *aliases):
        value = os.getenv(key)
        if value:
            return value
    return None


def _default_enable_llm() -> bool:
    raw = _get("NUGUARD_LLM", "AISBOM_ENABLE_LLM")
    if raw is not None:
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes", "on"}
    return False


def _default_llm_model() -> str:
    return _get("NUGUARD_LLM_MODEL", "AISBOM_LLM_MODEL") or "gpt-4o-mini"


def _default_llm_api_key() -> str | None:
    return _get("NUGUARD_LLM_API_KEY", "AISBOM_LLM_API_KEY")


def _default_llm_api_base() -> str | None:
    return _get("NUGUARD_LLM_API_BASE", "AISBOM_LLM_API_BASE")


def _default_budget_tokens() -> int:
    raw = _get("NUGUARD_LLM_BUDGET_TOKENS", "AISBOM_LLM_BUDGET_TOKENS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 500_000


def _default_gap_fill_max_calls() -> int:
    return _env_int("NUGUARD_GAP_FILL_MAX_CALLS", 40)


def _default_gap_fill_max_cost_usd() -> float:
    raw = os.getenv("NUGUARD_GAP_FILL_MAX_COST_USD")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 5.0


def _default_gap_fill_enable_privilege() -> bool:
    return _env_bool("NUGUARD_GAP_FILL_ENABLE_PRIVILEGE", False)


def _default_gap_fill_enable_guardrail() -> bool:
    return _env_bool("NUGUARD_GAP_FILL_ENABLE_GUARDRAIL", False)


def _default_verification_cost_budget() -> float:
    raw = os.getenv("AISBOM_VERIFICATION_COST_BUDGET")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 20.0


def _default_verification_max_verifications() -> int:
    return _env_int("AISBOM_MAX_VERIFICATIONS", 20)


def _default_llm_concurrency() -> int:
    """Max in-flight LLM calls during enrichment (issue #197).

    Defaults to 5 — matches the description-enrichment semaphore already in
    use. Override via ``NUGUARD_LLM_CONCURRENCY`` or ``AISBOM_LLM_CONCURRENCY``.
    """
    raw = _get("NUGUARD_LLM_CONCURRENCY", "AISBOM_LLM_CONCURRENCY")
    if raw:
        try:
            value = int(raw)
            if value >= 1:
                return value
        except ValueError:
            pass
    return 5


class AiSbomConfig(BaseModel):
    model_config = {"validate_assignment": True}
    # None = no limit on the number of files scanned/walked, for both local-folder
    # and GitHub repo discovery (they share the same _iter_files walker).
    max_files: int | None = Field(default=None, ge=1)
    max_file_size_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    include_extensions: set[str] = Field(
        default_factory=lambda: {
            ".py",
            ".go",
            ".java",
            ".pyw",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".ipynb",
            ".sql",
            ".json",
            ".yaml",
            ".yml",
            ".tf",
            ".tfvars",
            ".bicep",
            ".jinja",
            ".sh",
            ".bash",
            ".md",
            ".rs",
            ".rb",
            ".java",
            ".cs",
            ".toml",
            ".cfg",
        }
    )
    exclude_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns for files/directories to exclude from scanning, "
        "matched against the path relative to the scan root.",
    )
    honor_gitignore: bool = Field(
        default=True,
        description="When True, skip files matching .gitignore patterns in the repo root.",
    )
    enable_llm: bool = Field(default_factory=_default_enable_llm)

    # LLM enrichment (used when enable_llm=True)
    llm_model: str = Field(default_factory=_default_llm_model)
    llm_api_key: str | None = Field(default_factory=_default_llm_api_key)
    llm_api_base: str | None = Field(default_factory=_default_llm_api_base)
    llm_budget_tokens: int = Field(default_factory=_default_budget_tokens)
    llm_concurrency: int = Field(
        default_factory=_default_llm_concurrency,
        ge=1,
        le=64,
        description=(
            "Max in-flight LLM calls during enrichment (issue #197). "
            "Used to bound the per-node verification pass and any other "
            "independent LLM calls that can safely run in parallel."
        ),
    )

    # Gap-fill discovery pass (nuguard.yaml: sbom_generation.gap_fill.*)
    gap_fill_max_calls: int = Field(default_factory=_default_gap_fill_max_calls)
    gap_fill_max_cost_usd: float = Field(default_factory=_default_gap_fill_max_cost_usd)
    gap_fill_enable_privilege: bool = Field(default_factory=_default_gap_fill_enable_privilege)
    gap_fill_enable_guardrail: bool = Field(default_factory=_default_gap_fill_enable_guardrail)
    gap_fill_self_critique_categories: list[str] = Field(default_factory=list)

    # Node verification pass (nuguard.yaml: sbom_generation.verification.*)
    verification_cost_budget: float = Field(default_factory=_default_verification_cost_budget)
    verification_max_verifications: int = Field(
        default_factory=_default_verification_max_verifications
    )

    # Vertex AI / Google direct path (bypasses litellm when google_api_key is set)
    google_api_key: str | None = Field(
        default_factory=lambda: (
            os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_CLOUD_API_KEY") or None
        )
    )
    vertex_location: str | None = Field(
        default_factory=lambda: os.getenv("VERTEXAI_LOCATION") or None
    )

    supply_chain_scan: bool = Field(
        default=True,
        description=(
            "Run supply-chain second pass: DevToolConfigAdapter, GithubActionsAdapter, "
            "and LifecycleScriptAdapter. Creates DEVELOPER_TOOL_CONFIG, GITHUB_WORKFLOW, "
            "LIFECYCLE_SCRIPT, and MCP_SERVER nodes. Does not affect normal SBOM extraction."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        """Accept legacy ``deterministic_only`` input for compatibility."""
        if not isinstance(data, dict):
            return data
        if "deterministic_only" in data and "enable_llm" not in data:
            copied = dict(data)
            copied["enable_llm"] = not bool(copied.pop("deterministic_only"))
            return copied
        return data

    @property
    def deterministic_only(self) -> bool:
        """Backward-compatible view of the old configuration field."""
        return not self.enable_llm
