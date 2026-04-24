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
from typing import TYPE_CHECKING, Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nuguard.common.auth import LoginFlowConfig
from nuguard.common.errors import ConfigError
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig

_log = get_logger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _find_repo_root(start_dir: Path) -> Path | None:
    """Best-effort repository root discovery from a starting directory."""
    for candidate in [start_dir, *start_dir.parents]:
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return None


def _rebase_path(value: Any, base_dir: Path, repo_root: Path | None = None) -> Any:
    """Return an absolute path for relative string values.

    Leaves non-strings, empty strings, URLs, unresolved env placeholders, and
    already-absolute paths unchanged.
    """
    if not isinstance(value, str):
        return value
    if not value:
        return value

    # Keep unresolved env placeholders (e.g. ${VAR}) intact.
    if "${" in value:
        return value

    # Treat URI-like values as non-paths.
    if "://" in value:
        return value

    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)

    by_config_dir = (base_dir / path).resolve()
    by_repo_root = (repo_root / path).resolve() if repo_root else None

    # Prefer whichever candidate exists to preserve existing config behavior.
    if by_config_dir.exists():
        return str(by_config_dir)
    if by_repo_root is not None and by_repo_root.exists():
        return str(by_repo_root)

    # If neither exists yet (e.g. output files), prefer repo-root-relative when available.
    return str(by_repo_root if by_repo_root is not None else by_config_dir)


def _rebase_relative_paths(flat: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Rebase relative path-like config values against the config file dir."""
    path_keys = (
        "sbom",
        "source",
        "policy",
        "canary_path",
        "sarif_output_path",
        "redteam_prompt_cache_dir",
    )
    repo_root = _find_repo_root(base_dir)

    rebased = dict(flat)
    for key in path_keys:
        if key in rebased:
            rebased[key] = _rebase_path(rebased[key], base_dir, repo_root)

    behavior_cfg = rebased.get("behavior_config")
    if isinstance(behavior_cfg, dict):
        behavior_cfg = dict(behavior_cfg)
        if "canary" in behavior_cfg:
            behavior_cfg["canary"] = _rebase_path(behavior_cfg.get("canary"), base_dir, repo_root)
        auth = behavior_cfg.get("auth")
        if isinstance(auth, dict) and auth.get("cookie_file"):
            auth = dict(auth)
            auth["cookie_file"] = _rebase_path(auth["cookie_file"], base_dir, repo_root)
            behavior_cfg["auth"] = auth
        rebased["behavior_config"] = behavior_cfg

    return rebased


def _expand_env_var_token(token: str) -> str | None:
    """Expand a single ``${VAR}`` or ``${VAR:-default}`` token.

    Returns the resolved string, or ``None`` when the variable is unset and
    no default is provided.
    """
    if ":-" in token:
        var_name, default = token.split(":-", 1)
        return os.environ.get(var_name.strip(), default)
    return os.environ.get(token.strip())


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` references.

    When a referenced variable is not set and no ``:-default`` is given, the
    entire string is returned as ``None`` so that downstream consumers treat
    missing vars as absent rather than receiving an invalid placeholder string.
    """
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
            resolved = _expand_env_var_token(m.group(1))
            return resolved if resolved is not None else m.group(0)

        expanded = _ENV_VAR_RE.sub(_replace, value)
        # If any unexpanded ${...} placeholder remains, the variable was not
        # set — treat the whole value as absent.
        if _ENV_VAR_RE.search(expanded):
            return None
        return expanded
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

    # Top-level file-path keys
    for key in ("sbom", "source"):
        if key in data:
            flat[key] = data[key]

    # Policy: string path OR {path: ..., use_llm: bool}
    policy_val = data.get("policy")
    if isinstance(policy_val, str):
        flat["policy"] = policy_val
    elif isinstance(policy_val, dict):
        if "path" in policy_val:
            flat["policy"] = policy_val["path"]
        if "use_llm" in policy_val:
            flat["policy_use_llm"] = bool(policy_val["use_llm"])
        elif "llm" in policy_val:
            flat["policy_use_llm"] = bool(policy_val["llm"])

    # LLM section
    llm = data.get("llm", {}) or {}
    if "model" in llm:
        flat["litellm_model"] = llm["model"]
    if "api_key" in llm:
        flat["litellm_api_key"] = llm["api_key"]
    if "api_base" in llm:
        flat["litellm_api_base"] = llm["api_base"]

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
    if "chat_payload_key" in redteam:
        flat["redteam_chat_payload_key"] = redteam["chat_payload_key"]
    if "chat_payload_list" in redteam:
        flat["redteam_chat_payload_list"] = bool(redteam["chat_payload_list"])
    if "chat_response_key" in redteam:
        flat["redteam_chat_response_key"] = redteam["chat_response_key"]
    if "auth_header" in redteam:
        flat["redteam_auth_header"] = redteam["auth_header"]
    if "headers" in redteam and isinstance(redteam["headers"], dict):
        flat["redteam_headers"] = {
            str(k): str(v) for k, v in redteam["headers"].items()
        }
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
    if "request_timeout" in redteam:
        flat["redteam_request_timeout"] = float(redteam["request_timeout"])
    if "prompt_cache_dir" in redteam:
        flat["redteam_prompt_cache_dir"] = str(redteam["prompt_cache_dir"])
    if "app_env" in redteam and isinstance(redteam["app_env"], dict):
        flat["redteam_app_env"] = {
            str(k): str(v) for k, v in redteam["app_env"].items()
        }
    if "guided_conversations" in redteam:
        flat["redteam_guided_conversations"] = bool(redteam["guided_conversations"])
    if "guided_max_turns" in redteam:
        flat["redteam_guided_max_turns"] = int(redteam["guided_max_turns"])
    if "guided_concurrency" in redteam:
        flat["redteam_guided_concurrency"] = int(redteam["guided_concurrency"])
    if "guided_mutation_mode" in redteam:
        flat["redteam_guided_mutation_mode"] = str(redteam["guided_mutation_mode"])
    if "tree_breadth" in redteam:
        flat["redteam_tree_breadth"] = int(redteam["tree_breadth"])
    if "tree_max_depth" in redteam:
        flat["redteam_tree_max_depth"] = int(redteam["tree_max_depth"])
    if "emit_pytest" in redteam:
        flat["emit_pytest"] = bool(redteam["emit_pytest"])
    if "emit_pytest_dir" in redteam:
        flat["emit_pytest_dir"] = str(redteam["emit_pytest_dir"])
    if "guided_mutation_mode" in redteam:
        flat["redteam_guided_mutation_mode"] = str(redteam["guided_mutation_mode"])
    if "strict_outcome" in redteam:
        flat["redteam_strict_outcome"] = bool(redteam["strict_outcome"])
    if "credentials" in redteam and isinstance(redteam["credentials"], dict):
        flat["redteam_credentials"] = {
            str(k): str(v) for k, v in redteam["credentials"].items() if v is not None
        }
    finding_triggers = redteam.get("finding_triggers", {}) or {}
    if isinstance(finding_triggers, dict):
        if "canary_hits" in finding_triggers:
            flat["redteam_trigger_canary_hits"] = bool(finding_triggers["canary_hits"])
        if "policy_violations" in finding_triggers:
            flat["redteam_trigger_policy_violations"] = bool(
                finding_triggers["policy_violations"]
            )
        if "critical_success_hits" in finding_triggers:
            flat["redteam_trigger_critical_success_hits"] = bool(
                finding_triggers["critical_success_hits"]
            )
        if "any_inject_success" in finding_triggers:
            flat["redteam_trigger_any_inject_success"] = bool(
                finding_triggers["any_inject_success"]
            )

    # Redteam LLM section
    redteam_llm = redteam.get("llm", {}) or {}
    if "model" in redteam_llm:
        flat["redteam_llm_model"] = redteam_llm["model"]
    if "api_key" in redteam_llm:
        flat["redteam_llm_api_key"] = redteam_llm["api_key"]
    if "api_base" in redteam_llm:
        flat["redteam_llm_api_base"] = redteam_llm["api_base"]

    # Eval LLM section
    redteam_eval_llm = redteam.get("eval_llm", {}) or {}
    if "model" in redteam_eval_llm:
        flat["redteam_eval_llm_model"] = redteam_eval_llm["model"]
    if "api_key" in redteam_eval_llm:
        flat["redteam_eval_llm_api_key"] = redteam_eval_llm["api_key"]
    if "api_base" in redteam_eval_llm:
        flat["redteam_eval_llm_api_base"] = redteam_eval_llm["api_base"]

    # Behavior section
    if "behavior" in data:
        b = data.get("behavior") or {}
        if isinstance(b, dict):
            flat["behavior_config"] = b

    # Validate section
    if "validate" in data:
        v = data.get("validate") or {}
        if isinstance(v, dict):
            flat["validate_config"] = v

    # Redteam structured auth block
    redteam = data.get("redteam", {}) or {}
    if isinstance(redteam, dict) and "auth" in redteam:
        auth = redteam.get("auth") or {}
        if isinstance(auth, dict):
            auth_type = auth.get("type", "none")
            flat["redteam_auth_type"] = auth_type
            if auth_type in ("bearer", "api_key") and "header" in auth:
                flat["redteam_auth_header"] = auth["header"]
            if auth_type == "basic":
                flat["redteam_auth_username"] = auth.get("username") or ""
                flat["redteam_auth_password"] = auth.get("password") or ""
            if auth_type == "login_flow" and isinstance(auth.get("login_flow"), dict):
                flat["redteam_auth_login_flow"] = auth.get("login_flow")

    # Redteam defence_regressions
    if isinstance(redteam, dict) and "defence_regressions" in redteam:
        flat["redteam_defence_regressions"] = redteam["defence_regressions"]

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


class AppAuthConfig(BaseModel):
    """Structured auth config for behavior and redteam target requests."""

    type: Literal["bearer", "api_key", "basic", "none", "login_flow", "cookie_file"] = "none"
    header: str = ""
    username: str = ""
    password: str = ""
    login_flow: LoginFlowConfig | None = None
    # Path to a Netscape-format cookies.txt (absolute or relative to CWD)
    cookie_file: str = ""

    @field_validator("username", "password", "header", "cookie_file", mode="before")
    @classmethod
    def _coerce_none_to_empty(cls, v: object) -> str:
        """Treat None (from unexpanded ${ENV_VAR}) as an empty string."""
        return "" if v is None else v  # type: ignore[return-value]


class GoogleADKConfig(BaseModel):
    """Configuration for Google Agent Development Kit (ADK) framework integration.

    When an AI-SBOM reports ``google-adk`` in its ``summary.frameworks`` list,
    nuguard switches to the ADK-native protocol (``RunAgentRequest`` / ``POST /run``).
    These settings fine-tune that behaviour.

    Declare under ``behavior.adk:`` in ``nuguard.yaml``.

    Example::

        behavior:
          adk:
            app_name: marketing_campaign_agent
            user_id: nuguard-ci
            session_per_scenario: true
            run_path: /run
    """

    app_name: str = Field(
        default="",
        description=(
            "ADK application name used in RunAgentRequest.appName. "
            "When empty, nuguard attempts auto-discovery via GET /list-apps."
        ),
    )
    user_id: str = Field(
        default="nuguard",
        description="ADK user identifier injected into every RunAgentRequest.",
    )
    session_per_scenario: bool = Field(
        default=True,
        description=(
            "Create a fresh ADK session for each scenario so that conversation "
            "history does not bleed across test cases."
        ),
    )
    run_path: str = Field(
        default="/run",
        description="HTTP path for the ADK RunAgentRequest endpoint.",
    )


class BehaviorBoundaryAssertion(BaseModel):
    """Single boundary assertion declared in nuguard.yaml behavior.boundary_assertions."""

    name: str
    message: str
    expect: Literal["refused"] = "refused"
    forbid_pattern: str = ""


class BehaviorConfig(BaseModel):
    """Configuration for nuguard behavior mode."""

    target: str = ""
    target_endpoint: str = ""
    auth: AppAuthConfig = Field(default_factory=AppAuthConfig)
    canary: str = ""
    workflows: list[str] = Field(
        default_factory=list,
        description=(
            "Workflows to execute: intent_happy_path, component_coverage, "
            "boundary_enforcement, invariant_probe. "
            "Empty = run all."
        ),
    )
    boundary_assertions: list[BehaviorBoundaryAssertion] = Field(default_factory=list)
    request_timeout: float = 60.0
    verbose: bool = False
    use_llm: bool = Field(
        default=False,
        validation_alias=AliasChoices("use_llm", "llm"),
    )
    turn_delay_seconds: float = Field(
        default=0.0,
        description="Inter-turn pause in seconds to avoid 429 rate-limit errors.",
    )
    scenario_delay_seconds: float = Field(
        default=0.0,
        description="Pause between scenarios in seconds to avoid 429 rate-limit errors.",
    )
    chat_payload_key: str = "message"
    chat_payload_list: bool = False
    chat_payload_format: Literal["json", "form"] = "json"
    chat_response_key: str = ""
    adk: GoogleADKConfig = Field(
        default_factory=GoogleADKConfig,
        description="Google ADK-specific connection settings.",
    )
    credentials: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "App-level test credentials supplied automatically when the agent asks for them. "
            "Keys: username, password, api_key, token, otp, pin."
        ),
    )
    otel_endpoint: str | None = None
    otel_service_name: str | None = None

    # ------------------------------------------------------------------
    # v3 efficiency additions
    # ------------------------------------------------------------------

    scenario_concurrency: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Max simultaneous scenario HTTP sessions.",
    )
    judge_concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Max simultaneous judge LLM calls across all active scenarios.",
    )
    prompt_cache_dir: str = Field(
        default="",
        description=(
            "Directory for the cross-run scenario prompt cache. "
            "Empty string disables caching."
        ),
    )
    judge_cache_dir: str = Field(
        default="",
        description=(
            "Directory for the cross-run judge verdict cache. "
            "Empty string disables caching."
        ),
    )
    max_scenarios: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Hard cap on the total number of scenarios executed. "
            "Applied after deduplication, preserving priority order: L1, L2, L3, L4, L5. "
            "None (default) means no cap."
        ),
    )

    @field_validator("auth", mode="before")
    @classmethod
    def _coerce_none_auth(cls, v: Any) -> Any:
        """Convert None (auth: with all sub-keys commented out) to an empty dict."""
        return v if v is not None else {}

    @field_validator("credentials", mode="before")
    @classmethod
    def _drop_none_credentials(cls, v: Any) -> Any:
        """Strip entries whose value resolved to None (unset env vars without default)."""
        if isinstance(v, dict):
            return {k: val for k, val in v.items() if val is not None}
        return v or {}


class RedteamFindingTriggers(BaseModel):
    """Configurable controls for when redteam findings are emitted."""

    canary_hits: bool = True
    policy_violations: bool = True
    critical_success_hits: bool = True
    any_inject_success: bool = False

    def any_enabled(self) -> bool:
        """Return True when at least one trigger is enabled."""
        return any(
            [
                self.canary_hits,
                self.policy_violations,
                self.critical_success_hits,
                self.any_inject_success,
            ]
        )


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
    litellm_api_base: str | None = Field(
        default=None,
        description="Base URL override for the LLM provider (yaml: llm.api_base or AZURE_API_BASE env).",
        validation_alias=AliasChoices("AZURE_API_BASE", "litellm_api_base"),
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
        description="Default cognitive policy Markdown path (yaml: policy or policy.path).",
    )
    policy_use_llm: bool = Field(
        default=False,
        description="Use LLM to compile policy controls (yaml: policy.use_llm).",
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
        default="",
        description=(
            "Agent chat endpoint path on the target (yaml: redteam.target_endpoint). "
            "Empty string = auto-discover from SBOM; falls back to /chat."
        ),
    )
    redteam_chat_payload_key: str = Field(
        default="message",
        description=(
            "JSON key for the chat message in the POST body, e.g. 'message' or 'phrases' "
            "(yaml: redteam.chat_payload_key). Overrides SBOM auto-discovery."
        ),
    )
    redteam_chat_payload_list: bool = Field(
        default=False,
        description=(
            "When true, the chat message value is sent as a list rather than a plain string "
            "(yaml: redteam.chat_payload_list)."
        ),
    )
    redteam_chat_response_key: str = Field(
        default="",
        description=(
            "JSON key to extract response text from the target's reply body "
            "(yaml: redteam.chat_response_key). Empty string = auto-detect."
        ),
    )
    redteam_auth_header: str | None = Field(
        default=None,
        description=(
            "Legacy single-header auth for redteam requests, "
            "e.g. 'Authorization: Bearer ${TOKEN}' (yaml: redteam.auth_header)."
        ),
    )
    redteam_headers: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("NUGUARD_REDTEAM_HEADERS_JSON", "redteam_headers"),
        description=(
            "Explicit full header map override for redteam requests "
            "(yaml: redteam.headers, env: NUGUARD_REDTEAM_HEADERS_JSON)."
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
    redteam_request_timeout: float = Field(
        default=120.0,
        description=(
            "Per-request HTTP timeout in seconds for redteam chat/agent calls "
            "(yaml: redteam.request_timeout). Multi-agent pipelines can take "
            "60-120 s; increase further for very slow apps."
        ),
    )
    redteam_prompt_cache_dir: str = Field(
        default=".",
        validation_alias=AliasChoices(
            "NUGUARD_REDTEAM_PROMPT_CACHE_DIR",
            "redteam_prompt_cache_dir",
        ),
        description=(
            "Directory for redteam attack-payload prompt cache files "
            "(yaml: redteam.prompt_cache_dir, env: NUGUARD_REDTEAM_PROMPT_CACHE_DIR)."
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
    redteam_guided_conversations: bool = Field(
        default=True,
        description=(
            "Enable adaptive multi-turn guided conversations that steer the agent "
            "toward the attack goal based on its responses (yaml: redteam.guided_conversations). "
            "Requires redteam_llm_model to be set."
        ),
    )
    redteam_guided_max_turns: int = Field(
        default=12,
        description=(
            "Maximum turns per guided conversation before aborting "
            "(yaml: redteam.guided_max_turns)."
        ),
    )
    redteam_guided_concurrency: int = Field(
        default=3,
        description=(
            "Maximum number of guided conversations to run in parallel "
            "(yaml: redteam.guided_concurrency)."
        ),
    )
    redteam_guided_mutation_mode: Literal["soft", "hard"] = Field(
        default="hard",
        validation_alias=AliasChoices(
            "NUGUARD_REDTEAM_GUIDED_MUTATION_MODE",
            "redteam_guided_mutation_mode",
        ),
        description=(
            "Guided mutation prompt aggressiveness: soft|hard "
            "(yaml: redteam.guided_mutation_mode, "
            "env: NUGUARD_REDTEAM_GUIDED_MUTATION_MODE)."
        ),
    )
    redteam_tree_breadth: int = Field(
        default=0,
        description=(
            "TAP tree breadth — parallel attack variants per depth level. "
            "0 = auto (2 for ci profile, 3 for full). "
            "(yaml: redteam.tree_breadth)"
        ),
    )
    redteam_tree_max_depth: int = Field(
        default=0,
        description=(
            "TAP tree max depth — maximum recursion levels. "
            "0 = auto (2 for ci profile, 3 for full). "
            "(yaml: redteam.tree_max_depth)"
        ),
    )
    emit_pytest: bool = Field(
        default=False,
        description=(
            "Emit pytest regression test files for each successful HIT finding. "
            "(yaml: redteam.emit_pytest)"
        ),
    )
    emit_pytest_dir: str = Field(
        default="./redteam-regression",
        description=(
            "Directory to write generated pytest regression files. "
            "(yaml: redteam.emit_pytest_dir)"
        ),
    )
    redteam_strict_outcome: bool = Field(
        default=False,
        description=(
            "When true, a scan where ≥80 %% of transport events are server errors "
            "is reported as 'inconclusive_target_errors' rather than 'no_findings'. "
            "Disabled by default to preserve existing CI behaviour "
            "(yaml: redteam.strict_outcome)."
        ),
    )
    redteam_credentials: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "App-level test credentials supplied automatically when the agent asks for them "
            "during guided redteam conversations. "
            "Keys: username, password, api_key, token, otp, pin "
            "(yaml: redteam.credentials)."
        ),
    )
    redteam_trigger_canary_hits: bool = Field(
        default=True,
        description=(
            "Emit findings when canary values are detected in responses "
            "(yaml: redteam.finding_triggers.canary_hits)."
        ),
    )
    redteam_trigger_policy_violations: bool = Field(
        default=True,
        description=(
            "Emit findings for policy violations "
            "(yaml: redteam.finding_triggers.policy_violations)."
        ),
    )
    redteam_trigger_critical_success_hits: bool = Field(
        default=True,
        description=(
            "Emit fallback findings for high-confidence attack success signals "
            "(yaml: redteam.finding_triggers.critical_success_hits)."
        ),
    )
    redteam_trigger_any_inject_success: bool = Field(
        default=False,
        description=(
            "Emit findings when INJECT steps succeed even without canary/policy "
            "signals (yaml: redteam.finding_triggers.any_inject_success)."
        ),
    )
    redteam_llm_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NUGUARD_REDTEAM_LLM_MODEL", "redteam_llm_model"),
        description=(
            "LiteLLM model string for attack-payload generation. "
            "Must be an uncensored model (yaml: redteam.llm.model, "
            "env: NUGUARD_REDTEAM_LLM_MODEL)."
        ),
    )
    redteam_llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NUGUARD_REDTEAM_LLM_API_KEY", "redteam_llm_api_key"),
        description="API key for the redteam LLM (yaml: redteam.llm.api_key, env: NUGUARD_REDTEAM_LLM_API_KEY).",
    )
    redteam_llm_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NUGUARD_REDTEAM_LLM_API_BASE", "redteam_llm_api_base"),
        description=(
            "API base URL for the redteam LLM (required for Azure OpenAI). "
            "(yaml: redteam.llm.api_base, env: NUGUARD_REDTEAM_LLM_API_BASE)."
        ),
    )
    redteam_eval_llm_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NUGUARD_REDTEAM_EVAL_LLM_MODEL", "redteam_eval_llm_model"),
        description=(
            "LiteLLM model for response evaluation and summary generation. "
            "Defaults to top-level litellm_model (yaml: redteam.eval_llm.model, "
            "env: NUGUARD_REDTEAM_EVAL_LLM_MODEL)."
        ),
    )
    redteam_eval_llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NUGUARD_REDTEAM_EVAL_LLM_API_KEY", "redteam_eval_llm_api_key"),
        description=(
            "API key for the eval LLM. Defaults to litellm_api_key "
            r"(yaml: redteam.eval_llm.api_key, env: NUGUARD_REDTEAM_EVAL_LLM_API_KEY)."
        ),
    )
    redteam_eval_llm_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("NUGUARD_REDTEAM_EVAL_LLM_API_BASE", "redteam_eval_llm_api_base"),
        description=(
            "API base URL for the eval LLM (required for Azure OpenAI). "
            "(yaml: redteam.eval_llm.api_base, env: NUGUARD_REDTEAM_EVAL_LLM_API_BASE)."
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

    # ------------------------------------------------------- Behavior mode
    behavior_config: BehaviorConfig = Field(
        default_factory=BehaviorConfig,
        description="Configuration for nuguard behavior mode.",
    )

    # ----------------------------------------- Structured redteam auth
    redteam_auth_type: str = Field(
        default="none",
        description=(
            "Structured auth type for redteam: "
            "bearer|api_key|basic|none|login_flow (yaml: redteam.auth.type)."
        ),
    )
    redteam_auth_username: str = Field(
        default="",
        description="Username for redteam basic auth (yaml: redteam.auth.username).",
    )
    redteam_auth_password: str = Field(
        default="",
        description="Password for redteam basic auth (yaml: redteam.auth.password).",
    )
    redteam_auth_login_flow: LoginFlowConfig | None = Field(
        default=None,
        description="Login-flow auth config for redteam (yaml: redteam.auth.login_flow).",
    )

    # ----------------------------------------- Defence regressions
    redteam_defence_regressions: list[dict] = Field(
        default_factory=list,
        description="Defence regression scenarios declared in nuguard.yaml redteam.defence_regressions.",
    )

    def resolved_auth_config(self) -> "AuthConfig":
        """Build an AuthConfig from the resolved *redteam* auth settings.

        Priority: structured auth block > legacy auth_header string > none.
        Import is deferred to avoid circular imports.
        """
        from nuguard.common.auth import AuthConfig

        if self.redteam_auth_type and self.redteam_auth_type != "none":
            return AuthConfig(
                type=self.redteam_auth_type,  # type: ignore[arg-type]
                header=self.redteam_auth_header or "",
                username=self.redteam_auth_username,
                password=self.redteam_auth_password,
                login_flow=self.redteam_auth_login_flow,
            )
        if self.redteam_auth_header:
            return AuthConfig.from_header_string(self.redteam_auth_header)
        return AuthConfig(type="none")

    def resolved_redteam_finding_triggers(self) -> RedteamFindingTriggers:
        """Build trigger controls from resolved redteam configuration."""
        return RedteamFindingTriggers(
            canary_hits=self.redteam_trigger_canary_hits,
            policy_violations=self.redteam_trigger_policy_violations,
            critical_success_hits=self.redteam_trigger_critical_success_hits,
            any_inject_success=self.redteam_trigger_any_inject_success,
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
        yaml_overrides = _rebase_relative_paths(yaml_overrides, candidate.parent)
        break

    return NuGuardConfig(**yaml_overrides)
