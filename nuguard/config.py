"""Configuration resolution for nuguard.

Priority order (highest wins):
    CLI flags  >  nuguard.yaml  >  environment variables  >  built-in defaults

The ``load_config`` function handles loading ``nuguard.yaml`` and merging it
with environment variables.  CLI flags are applied by callers after the fact by
mutating the returned ``NuGuardConfig`` instance.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from nuguard.common.errors import ConfigError
from nuguard.common.logging import get_logger

_log = get_logger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references in strings using ``os.environ``."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)), value
        )
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def _flatten_yaml(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten a nuguard.yaml dict into env-style keys for pydantic-settings.

    Handles nested sections like ``llm.model`` → ``litellm_model``.
    """
    flat: dict[str, Any] = {}

    # Top-level file-path keys — kept as their YAML names (matched via field aliases)
    for key in ("sbom", "source", "policy"):
        if key in data:
            flat[key] = data[key]

    # LLM section
    llm = data.get("llm", {}) or {}
    if "model" in llm:
        flat["litellm_model"] = llm["model"]
    if "api_key" in llm:
        flat["litellm_api_key"] = llm["api_key"]

    # Database section
    db = data.get("database", {}) or {}
    if "url" in db:
        flat["database_url"] = db["url"]

    # SBOM generation section
    sbom_gen = data.get("sbom_generation", {}) or {}
    if "llm" in sbom_gen:
        flat["sbom_llm_enabled"] = bool(sbom_gen["llm"])

    # Redteam section
    redteam = data.get("redteam", {}) or {}
    if "target" in redteam:
        flat["target_url"] = redteam["target"]
    if "target_endpoint" in redteam:
        flat["target_endpoint"] = redteam["target_endpoint"]
    if "auth_header" in redteam:
        flat["redteam_auth_header"] = redteam["auth_header"]
    if "canary" in redteam:
        flat["canary_path"] = redteam["canary"]
    if "profile" in redteam:
        flat["redteam_profile"] = redteam["profile"]
    if "min_impact_score" in redteam:
        flat["min_impact_score"] = float(redteam["min_impact_score"])
    if "scenarios" in redteam:
        flat["redteam_scenarios"] = redteam["scenarios"]
    if "mcp_trusted_servers" in redteam:
        flat["mcp_trusted_servers"] = redteam["mcp_trusted_servers"]
    if "verbose" in redteam:
        flat["redteam_verbose"] = bool(redteam["verbose"])
    if "app_env" in redteam and isinstance(redteam["app_env"], dict):
        flat["redteam_app_env"] = {
            str(k): str(v) for k, v in redteam["app_env"].items()
        }

    # Analyze section
    analyze = data.get("analyze", {}) or {}
    if "min_severity" in analyze:
        flat["analyze_min_severity"] = analyze["min_severity"]

    # Output section
    output = data.get("output", {}) or {}
    if "format" in output:
        flat["output_format"] = output["format"]
    if "fail_on" in output:
        flat["fail_on"] = output["fail_on"]
    if "sarif_file" in output:
        flat["sarif_output_path"] = output["sarif_file"]

    return flat


class NuGuardConfig(BaseSettings):
    """Resolved nuguard configuration.

    Fields are populated from environment variables and/or ``nuguard.yaml``
    via :func:`load_config`.
    """

    # ------------------------------------------------------------------ LLM
    litellm_model: str = Field(
        default="gemini/gemini-2.0-flash",
        description="LiteLLM model string (yaml: llm.model).",
    )
    litellm_api_key: str | None = Field(
        default=None,
        description="API key for the model provider (yaml: llm.api_key or LITELLM_API_KEY env).",
    )

    # ------------------------------------------------------------ Database
    database_url: str | None = Field(
        default=None,
        description="SQLAlchemy async database URL (yaml: database.url). None = SQLite default.",
    )

    # ------------------------------------------------- Project file paths
    sbom_path: str | None = Field(
        default=None,
        alias="sbom",
        description="Default AI-SBOM JSON path (yaml: sbom).",
    )
    source_path: str | None = Field(
        default=None,
        alias="source",
        description="Default application source path for SBOM generation (yaml: source).",
    )
    policy_path: str | None = Field(
        default=None,
        alias="policy",
        description="Default cognitive policy Markdown path (yaml: policy).",
    )

    # ------------------------------------------------- SBOM generation
    sbom_llm_enabled: bool = Field(
        default=False,
        description="Enable LLM enrichment during SBOM generation (yaml: sbom_generation.llm).",
    )

    # ------------------------------------------------------- Redteam
    target_url: str | None = Field(
        default=None,
        description="URL of the running AI application under test (yaml: redteam.target).",
    )
    target_endpoint: str = Field(
        default="/chat",
        description="Agent chat endpoint path on the target (yaml: redteam.target_endpoint).",
    )
    redteam_auth_header: str | None = Field(
        default=None,
        description=(
            "Authorization header to include with each redteam request, "
            "e.g. 'Authorization: Bearer ${TOKEN}' (yaml: redteam.auth_header)."
        ),
    )
    canary_path: str | None = Field(
        default=None,
        description="Path to canary JSON file (yaml: redteam.canary).",
    )
    redteam_profile: str = Field(
        default="ci",
        description="Scan profile: 'ci' (fast, ≥5 impact) or 'full' (all scenarios) (yaml: redteam.profile).",
    )
    min_impact_score: float = Field(
        default=0.0,
        description="Minimum pre-score [0–10] for scenario inclusion (yaml: redteam.min_impact_score).",
    )
    redteam_scenarios: list[str] = Field(
        default_factory=list,
        description=(
            "Scenario types to run; empty = all. "
            "Values: prompt-injection, tool-abuse, privilege-escalation, "
            "data-exfiltration, policy-violation, mcp-toxic-flow "
            "(yaml: redteam.scenarios)."
        ),
    )
    mcp_trusted_servers: list[str] = Field(
        default_factory=list,
        description=(
            "MCP server hostnames treated as trusted. "
            "Servers absent from this list are classified 'untrusted' "
            "and eligible as toxic-flow sources (yaml: redteam.mcp_trusted_servers)."
        ),
    )
    redteam_verbose: bool = Field(
        default=False,
        description=(
            "Include full per-scenario traces (inputs, outputs, selection rationale, "
            "risk scores) in the redteam report (yaml: redteam.verbose). "
            "Also enabled by NUGUARD_REDTEAM_VERBOSE=1 env var."
        ),
    )
    redteam_app_env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Extra environment variables injected into the fixture app subprocess "
            "during E2E redteam tests (yaml: redteam.app_env). "
            r"Use ${VAR} interpolation to avoid storing secrets in the file."
        ),
    )

    # ------------------------------------------------------- Analyze
    analyze_min_severity: str = Field(
        default="medium",
        description=(
            "Minimum severity for analysis findings: critical|high|medium|low "
            "(yaml: analyze.min_severity)."
        ),
    )

    # ------------------------------------------------------- Output
    output_format: str = Field(
        default="text",
        description="Default output format: text|json|markdown|sarif (yaml: output.format).",
    )
    fail_on: str = Field(
        default="high",
        description=(
            "Exit non-zero when any finding meets this severity: "
            "critical|high|medium|low (yaml: output.fail_on)."
        ),
    )
    sarif_output_path: str | None = Field(
        default=None,
        description="Path to write SARIF output (yaml: output.sarif_file).",
    )

    model_config = SettingsConfigDict(
        env_file=".nuguard.env",
        extra="ignore",
        populate_by_name=True,
    )


def load_config(config_file: Path | None = None) -> NuGuardConfig:
    """Load ``nuguard.yaml``, merge with env vars, and return ``NuGuardConfig``.

    Resolution steps:
    1. Locate ``nuguard.yaml`` in cwd or use *config_file* if provided.
    2. Parse YAML and expand ``${VAR}`` references from the environment.
    3. Flatten nested YAML structure into pydantic-settings-compatible keys.
    4. Build and return a :class:`NuGuardConfig` from the merged dict.

    Args:
        config_file: Explicit path to ``nuguard.yaml``.  When ``None`` the
                     function tries ``./nuguard.yaml`` in the current
                     directory.

    Returns:
        Resolved :class:`NuGuardConfig`.

    Raises:
        ConfigError: When *config_file* is provided but does not exist, or
                     when the YAML is malformed.
    """
    yaml_overrides: dict[str, Any] = {}

    candidates = [config_file] if config_file else [Path("nuguard.yaml")]
    for candidate in candidates:
        if candidate is None:
            continue
        candidate = Path(candidate)
        if not candidate.exists():
            if config_file is not None:
                raise ConfigError(f"Config file not found: {config_file}")
            _log.debug("No nuguard.yaml found in cwd — using env vars and defaults.")
            break
        _log.debug("Loading config from %s", candidate)
        try:
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse {candidate}: {exc}") from exc

        expanded = _expand_env_vars(raw)
        yaml_overrides = _flatten_yaml(expanded)
        break

    return NuGuardConfig(**yaml_overrides)
