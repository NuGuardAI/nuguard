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
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nuguard.common import chat_payload_tokens
from nuguard.common.auth import LoginFlowConfig
from nuguard.common.errors import ConfigError
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig

_log = get_logger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _validate_chat_payload_extras(extras: dict[str, Any], source: str) -> None:
    """Raise :class:`ConfigError` if *extras* contains an unrecognized ``{{...}}`` token.

    Catches typos (e.g. ``{{mesage}}``) or a token embedded in a larger string
    (e.g. ``"Hi {{message}}"``, which substitution never resolves) at config-load
    time instead of sending the literal placeholder text to the target app.
    """
    unrecognized = chat_payload_tokens.find_unrecognized(extras, chat_payload_tokens.EXTRAS_RECOGNIZED)
    if unrecognized:
        raise ConfigError(
            f"{source}.chat_payload_extras contains unrecognized slot token(s): "
            f"{sorted(unrecognized)!r}. Recognized tokens: "
            f"{sorted(chat_payload_tokens.EXTRAS_RECOGNIZED)!r}."
        )


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
        "redteam_catalog_path",
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

    # LLM section — skip keys whose env-var interpolation produced None (var not set)
    llm = data.get("llm", {}) or {}
    if llm.get("model") is not None:
        flat["litellm_model"] = llm["model"]
    if llm.get("api_key") is not None:
        flat["litellm_api_key"] = llm["api_key"]
    if llm.get("api_base") is not None:
        flat["litellm_api_base"] = llm["api_base"]

    # Database section
    db = data.get("database", {}) or {}
    if "url" in db:
        flat["database_url"] = db["url"]

    # SBOM generation section
    sbom_gen = data.get("sbom_generation", {}) or {}
    if "llm" in sbom_gen:
        flat["sbom_llm_enabled"] = bool(sbom_gen["llm"])
    if "llm_concurrency" in sbom_gen:
        flat["sbom_llm_concurrency"] = int(sbom_gen["llm_concurrency"])

    gap_fill = sbom_gen.get("gap_fill", {}) or {}
    if "max_calls" in gap_fill:
        flat["sbom_gap_fill_max_calls"] = int(gap_fill["max_calls"])
    if "max_cost_usd" in gap_fill:
        flat["sbom_gap_fill_max_cost_usd"] = float(gap_fill["max_cost_usd"])
    if "enable_privilege" in gap_fill:
        flat["sbom_gap_fill_enable_privilege"] = bool(gap_fill["enable_privilege"])
    if "enable_guardrail" in gap_fill:
        flat["sbom_gap_fill_enable_guardrail"] = bool(gap_fill["enable_guardrail"])
    if "self_critique_categories" in gap_fill:
        flat["sbom_gap_fill_self_critique_categories"] = list(
            gap_fill["self_critique_categories"] or []
        )

    sbom_verification = sbom_gen.get("verification", {}) or {}
    if "max_verifications" in sbom_verification:
        flat["sbom_verification_max_verifications"] = int(sbom_verification["max_verifications"])
    if "cost_budget" in sbom_verification:
        flat["sbom_verification_cost_budget"] = float(sbom_verification["cost_budget"])

    # ── Shared target block ────────────────────────────────────────────────────
    # target: at the top level acts as a shared default for both behavior and
    # redteam.  Section-level keys (redteam.target, behavior.target) take
    # precedence and override the shared values when present.
    shared_target = data.get("target", {}) or {}
    if isinstance(shared_target, dict):
        if "url" in shared_target:
            flat["target_url"] = shared_target["url"]
        if "endpoint" in shared_target:
            flat["target_endpoint"] = shared_target["endpoint"]
        elif "target_endpoint" in shared_target:
            # Accept target_endpoint as an alias for endpoint in the shared target block
            flat["target_endpoint"] = shared_target["target_endpoint"]
        if "chat_payload_key" in shared_target:
            flat["redteam_chat_payload_key"] = shared_target["chat_payload_key"]
        if "chat_payload_list" in shared_target:
            flat["redteam_chat_payload_list"] = bool(shared_target["chat_payload_list"])
        if "chat_response_key" in shared_target:
            flat["redteam_chat_response_key"] = shared_target["chat_response_key"]
        if "chat_payload_extras" in shared_target and isinstance(shared_target["chat_payload_extras"], dict):
            _validate_chat_payload_extras(shared_target["chat_payload_extras"], "target")
            flat["redteam_chat_payload_extras"] = shared_target["chat_payload_extras"]
        if "headers" in shared_target and isinstance(shared_target["headers"], dict):
            flat["redteam_headers"] = {
                str(k): str(v)
                for k, v in shared_target["headers"].items()
                if v is not None
            }
        # Structured auth from the shared block — same format as behavior/redteam auth
        _shared_auth = shared_target.get("auth", {}) or {}
        if isinstance(_shared_auth, dict) and _shared_auth:
            _sa_type = _shared_auth.get("type", "none")
            flat["redteam_auth_type"] = _sa_type
            if _sa_type in ("bearer", "api_key") and "header" in _shared_auth:
                flat["redteam_auth_header"] = _shared_auth["header"]
            if _sa_type == "basic":
                flat["redteam_auth_username"] = _shared_auth.get("username") or ""
                flat["redteam_auth_password"] = _shared_auth.get("password") or ""
            if _sa_type == "login_flow" and isinstance(_shared_auth.get("login_flow"), dict):
                flat["redteam_auth_login_flow"] = _shared_auth["login_flow"]

    # Redteam section — overrides shared target block when keys are present.
    # Drop keys whose env-var interpolation resolved to None (unset ${VAR}
    # without a ``:-default``) so scalar fields keep their model defaults
    # instead of crashing validation as the literal string 'None' — matching
    # the established credentials/LLM-block handling below.
    # Both `endpoint:` and `target_endpoint:` are accepted as aliases, mirroring
    # the shared ``target:`` block (which accepts both names). The shared block
    # already does this on lines 199-203; the override must accept the same
    # aliases so users who wrote ``endpoint:`` (the canonical form documented
    # in nuguard.yaml.example line 47) see their override take effect.
    # Precedence matches the shared block: ``endpoint`` wins over
    # ``target_endpoint`` when both are set in the same block.
    redteam = {k: v for k, v in (data.get("redteam", {}) or {}).items() if v is not None}
    if "target" in redteam:
        flat["target_url"] = redteam["target"]
    if "endpoint" in redteam:
        flat["target_endpoint"] = redteam["endpoint"]
    elif "target_endpoint" in redteam:
        flat["target_endpoint"] = redteam["target_endpoint"]
    if "chat_payload_key" in redteam:
        flat["redteam_chat_payload_key"] = redteam["chat_payload_key"]
    if "chat_payload_list" in redteam:
        flat["redteam_chat_payload_list"] = bool(redteam["chat_payload_list"])
    if "chat_response_key" in redteam:
        flat["redteam_chat_response_key"] = redteam["chat_response_key"]
    if "chat_payload_extras" in redteam and isinstance(redteam["chat_payload_extras"], dict):
        _validate_chat_payload_extras(redteam["chat_payload_extras"], "redteam")
        flat["redteam_chat_payload_extras"] = redteam["chat_payload_extras"]
    if "auth_header" in redteam:
        flat["redteam_auth_header"] = redteam["auth_header"]
    if "headers" in redteam and isinstance(redteam["headers"], dict):
        flat["redteam_headers"] = {
            str(k): str(v)
            for k, v in redteam["headers"].items()
            if v is not None
        }
    if "canary" in redteam:
        flat["canary_path"] = redteam["canary"]
    # `redteam.engine` is silently ignored — v2 was removed (issue #216); the
    # only engine is v1. Keep parsing the key for back-compat with older
    # nuguard.yaml files.
    if "profile" in redteam:
        flat["redteam_profile"] = redteam["profile"]
    if "mode" in redteam:
        flat["redteam_mode"] = str(redteam["mode"])
    if "progressive" in redteam and isinstance(redteam["progressive"], dict):
        _prog = redteam["progressive"]
        if "halt_on_severity" in _prog:
            flat["redteam_progressive_halt_on_severity"] = str(_prog["halt_on_severity"])
    if "catalog_path" in redteam:
        flat["redteam_catalog_path"] = str(redteam["catalog_path"])
    if "min_impact_score" in redteam:
        flat["min_impact_score"] = float(redteam["min_impact_score"])
    if "scenarios" in redteam:
        scenarios = redteam["scenarios"]
        if isinstance(scenarios, list):
            # Unset ${VAR} entries resolve to None — drop them so the
            # remaining explicit goals still run instead of failing validation.
            scenarios = [s for s in scenarios if s is not None]
        flat["redteam_scenarios"] = scenarios
    if "mcp_trusted_servers" in redteam:
        flat["mcp_trusted_servers"] = redteam["mcp_trusted_servers"]
    if "verbose" in redteam:
        flat["redteam_verbose"] = bool(redteam["verbose"])
    if "request_timeout" in redteam:
        flat["redteam_request_timeout"] = float(redteam["request_timeout"])
    if "scenario_timeout" in redteam:
        flat["redteam_scenario_timeout"] = float(redteam["scenario_timeout"])
    if "similar_miss_threshold" in redteam:
        flat["redteam_similar_miss_threshold"] = int(redteam["similar_miss_threshold"])
    if "hard_refusal_abort_turns" in redteam:
        flat["redteam_hard_refusal_abort_turns"] = int(redteam["hard_refusal_abort_turns"])
    if "stall_abort_threshold" in redteam:
        flat["redteam_stall_abort_threshold"] = int(redteam["stall_abort_threshold"])
    if "skip_discovery" in redteam:
        flat["redteam_skip_discovery"] = bool(redteam["skip_discovery"])
    if "discovery_max_turns" in redteam:
        flat["redteam_discovery_max_turns"] = int(redteam["discovery_max_turns"])
    if "capability_discovery" in redteam:
        flat["redteam_capability_discovery"] = bool(redteam["capability_discovery"])
    if "prompt_cache_dir" in redteam:
        flat["redteam_prompt_cache_dir"] = str(redteam["prompt_cache_dir"])
    if "app_env" in redteam and isinstance(redteam["app_env"], dict):
        flat["redteam_app_env"] = {
            str(k): str(v)
            for k, v in redteam["app_env"].items()
            if v is not None
        }
    if "customer_profile" in redteam and redteam["customer_profile"] is not None:
        app_env = dict(flat.get("redteam_app_env", {}))
        app_env["BLISSFUL_CUSTOMER_PROFILE"] = str(redteam["customer_profile"])
        flat["redteam_app_env"] = app_env
    if "guided_conversations" in redteam:
        flat["redteam_guided_conversations"] = bool(redteam["guided_conversations"])
    if "guided_max_turns" in redteam:
        flat["redteam_guided_max_turns"] = int(redteam["guided_max_turns"])
    if "guided_concurrency" in redteam:
        flat["redteam_guided_concurrency"] = int(redteam["guided_concurrency"])
    if "concurrency" in redteam:
        flat["redteam_concurrency"] = int(redteam["concurrency"])
    if "turn_delay_seconds" in redteam:
        flat["redteam_turn_delay_seconds"] = float(redteam["turn_delay_seconds"])
    if "codegen_escalation_enabled" in redteam:
        flat["redteam_codegen_escalation_enabled"] = bool(redteam["codegen_escalation_enabled"])
    if "max_concurrent_requests" in redteam:
        flat["redteam_max_concurrent_requests"] = int(redteam["max_concurrent_requests"])
    if "max_transient_hold_seconds" in redteam:
        flat["redteam_max_transient_hold_seconds"] = float(redteam["max_transient_hold_seconds"])
    if "scenario_delay_seconds" in redteam:
        flat["redteam_scenario_delay_seconds"] = float(redteam["scenario_delay_seconds"])
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
    if "pre_run_warmup" in redteam:
        flat["redteam_pre_run_warmup"] = int(redteam["pre_run_warmup"])
    if "verify_findings" in redteam:
        flat["redteam_verify_findings"] = bool(redteam["verify_findings"])
    if "suppress_spa_html_auth_bypass" in redteam:
        flat["redteam_suppress_spa_html_auth_bypass"] = bool(redteam["suppress_spa_html_auth_bypass"])
    if "golden_data" in redteam and isinstance(redteam["golden_data"], dict):
        flat["redteam_golden_data"] = redteam["golden_data"]
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

    # Redteam LLM section — skip keys whose env-var interpolation produced None
    redteam_llm = redteam.get("llm", {}) or {}
    if redteam_llm.get("model") is not None:
        flat["redteam_llm_model"] = redteam_llm["model"]
    if redteam_llm.get("api_key") is not None:
        flat["redteam_llm_api_key"] = redteam_llm["api_key"]
    if redteam_llm.get("api_base") is not None:
        flat["redteam_llm_api_base"] = redteam_llm["api_base"]

    # Eval LLM section — skip keys whose env-var interpolation produced None
    redteam_eval_llm = redteam.get("eval_llm", {}) or {}
    if redteam_eval_llm.get("model") is not None:
        flat["redteam_eval_llm_model"] = redteam_eval_llm["model"]
    if redteam_eval_llm.get("api_key") is not None:
        flat["redteam_eval_llm_api_key"] = redteam_eval_llm["api_key"]
    if redteam_eval_llm.get("api_base") is not None:
        flat["redteam_eval_llm_api_base"] = redteam_eval_llm["api_base"]

    # Behavior section — shared target block keys are the baseline; behavior.* overrides them
    if "behavior" in data:
        b = data.get("behavior") or {}
        if isinstance(b, dict):
            # A YAML key written with no value (e.g. "workflows:") parses to None,
            # not an empty list/string — that almost always means "leave this at
            # its default", not "explicitly set to null". Drop such keys so
            # BehaviorConfig's own defaults (e.g. workflows: [] = run all) apply
            # instead of failing type validation.
            b = {k: val for k, val in b.items() if val is not None}
            # Inject shared target fields as defaults into the behavior dict so that
            # BehaviorConfig picks them up without requiring duplication in nuguard.yaml.
            # Keys already present in the behavior block take precedence.
            if isinstance(shared_target, dict):
                _shared_for_behavior: dict[str, Any] = {}
                if "url" in shared_target:
                    _shared_for_behavior["target"] = shared_target["url"]
                if "endpoint" in shared_target:
                    _shared_for_behavior["target_endpoint"] = shared_target["endpoint"]
                elif "target_endpoint" in shared_target:
                    _shared_for_behavior["target_endpoint"] = shared_target["target_endpoint"]
                if "chat_payload_key" in shared_target:
                    _shared_for_behavior["chat_payload_key"] = shared_target["chat_payload_key"]
                if "chat_payload_list" in shared_target:
                    _shared_for_behavior["chat_payload_list"] = bool(
                        shared_target["chat_payload_list"]
                    )
                if "chat_response_key" in shared_target:
                    _shared_for_behavior["chat_response_key"] = shared_target["chat_response_key"]
                if "chat_payload_extras" in shared_target and isinstance(shared_target["chat_payload_extras"], dict):
                    _shared_for_behavior.setdefault("chat_payload_extras", shared_target["chat_payload_extras"])
                if "headers" in shared_target and isinstance(shared_target["headers"], dict):
                    _shared_for_behavior.setdefault(
                        "headers",
                        {
                            str(k): str(v)
                            for k, v in shared_target["headers"].items()
                            if v is not None
                        },
                    )
                if "auth" in shared_target and isinstance(shared_target["auth"], dict):
                    _shared_for_behavior.setdefault("auth", shared_target["auth"])
                # behavior.* wins — merge shared as the lower-precedence base.
                # `endpoint:` and `target_endpoint:` are aliases in the shared
                # block; mirror that here so a user who writes `behavior.endpoint:`
                # to override the shared endpoint actually wins the merge.
                # Precedence matches the shared block on lines 410-413:
                # ``endpoint`` wins over ``target_endpoint`` when both are
                # set in the same block, so we collapse ``endpoint`` into
                # ``target_endpoint`` unconditionally if it's present.
                if "endpoint" in b:
                    b["target_endpoint"] = b["endpoint"]
                b = {**_shared_for_behavior, **b}
            if isinstance(b, dict) and isinstance(b.get("headers"), dict):
                b["headers"] = {
                    str(k): str(v)
                    for k, v in b["headers"].items()
                    if v is not None
                }
            if isinstance(b, dict) and isinstance(b.get("chat_payload_extras"), dict):
                _validate_chat_payload_extras(b["chat_payload_extras"], "behavior")
            flat["behavior_config"] = b

    # Validate section
    if "validate" in data:
        v = data.get("validate") or {}
        if isinstance(v, dict):
            # Same empty-key footgun as the behavior section above.
            v = {k: val for k, val in v.items() if val is not None}
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
    if "nga_only" in analyze:
        flat["analyze_nga_only"] = analyze["nga_only"]
    if "supply_chain_profile" in analyze:
        flat["analyze_supply_chain_profile"] = analyze["supply_chain_profile"]
    if "supply_chain_verify" in analyze:
        flat["analyze_supply_chain_verify"] = analyze["supply_chain_verify"]

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

    enabled: bool = Field(
        default=True,
        description=(
            "Set false to disable the ADK adapter entirely and use generic HTTP POST. "
            "Use when the target wraps ADK internally but exposes a custom REST API "
            "(not the standard /apps/{name}/users/{uid}/sessions ADK endpoints)."
        ),
    )
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
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Extra HTTP headers added to every behavior request "
            "(yaml: behavior.headers, or the shared target.headers block)."
        ),
    )
    canary: str = ""
    workflows: list[str] = Field(
        default_factory=list,
        description=(
            "Workflow categories to execute: topic_coverage, agent_tool_coverage, "
            "guardrail_coverage, data_discovery_probe. Empty = run all."
        ),
    )
    boundary_assertions: list[BehaviorBoundaryAssertion] = Field(default_factory=list)
    golden_data: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Pre-seeded user profile data for behavior testing — same schema as "
            "redteam.golden_data.  Used as a fallback when live pre-scan discovery "
            "returns an empty profile so test payloads reference real account IDs and "
            "names rather than synthetic placeholders."
        ),
    )
    request_timeout: float = 60.0
    verbose: bool = False
    use_llm: bool = Field(
        default=False,
        validation_alias=AliasChoices("use_llm", "llm"),
    )
    capability_discovery: bool = Field(
        default=True,
        description=(
            "Probe the live agent for tools, sub-agents, and its system prompt when the "
            "AI-SBOM is missing them, and merge the findings back into the in-memory SBOM "
            "before scenario generation. Only fires for AGENT nodes with an actual gap."
        ),
    )
    turn_delay_seconds: float = Field(
        default=0.0,
        description="Inter-turn pause in seconds to avoid 429 rate-limit errors.",
    )
    scenario_delay_seconds: float = Field(
        default=0.0,
        description="Pause between scenarios in seconds to avoid 429 rate-limit errors.",
    )
    coverage_turns_per_scenario: int = Field(
        default=5,
        ge=1,
        description=(
            "Max adaptive coverage-turns appended to a scenario to probe SBOM "
            "components not yet exercised by its scripted messages."
        ),
    )
    max_session_turns: int = Field(
        default=10,
        ge=1,
        description="Hard cap on total turns (scripted + coverage) in a single scenario session.",
    )
    tool_chain_size: int = Field(
        default=4,
        ge=1,
        description=(
            "Max tools grouped into a single tool_coverage/component_coverage scenario "
            "chain. Larger values produce fewer, longer multi-turn scenarios for the "
            "same tool coverage."
        ),
    )
    guided_coverage: bool = Field(
        default=False,
        description=(
            "Use a live, LLM-steered conversation (CoverageDirector) to exercise "
            "uncovered agents/tools turn-by-turn instead of pre-generated tool-chain "
            "scripts. Produces more natural conversations at the cost of extra "
            "adaptive LLM calls per turn."
        ),
    )
    chat_payload_key: str = "message"
    chat_payload_list: bool = False
    chat_payload_format: Literal["json", "form"] = "json"
    chat_response_key: str = ""
    chat_payload_extras: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extra static JSON fields merged into every chat POST body "
            "(e.g. vehicleState, language). The message key always takes precedence."
        ),
    )
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

    endpoint_aliases: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Manual overrides mapping a runtime endpoint route to its canonical "
            "SBOM API_ENDPOINT component name, e.g. {'/api/agent/chat': '/chat API'}."
        ),
    )
    component_aliases: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Manual overrides mapping a free-text agent/tool mention seen in live "
            "agent responses to its canonical SBOM component name, e.g. "
            "{'Nova': 'Fintech App Assistant'}. Use this for persona/brand names "
            "with no textual overlap with the SBOM's structural component name — "
            "fuzzy and descriptive-name matching cannot infer these on their own."
        ),
    )
    sole_agent_alias_fallback: bool = Field(
        default=True,
        description=(
            "When the SBOM declares exactly one AGENT node, attribute any "
            "otherwise-unmatched agent mention to it (handles a live app's "
            "self-chosen persona name that differs entirely from the SBOM name). "
            "Disable if the single agent legitimately delegates to unlisted "
            "sub-agents outside the SBOM."
        ),
    )

    # ------------------------------------------------------------------
    # v3 efficiency additions
    # ------------------------------------------------------------------

    scenario_concurrency: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Max simultaneous scenario HTTP sessions.",
    )
    isolate_scenarios: bool = Field(
        default=True,
        description=(
            "Re-authenticate before each scenario to prevent cross-scenario "
            "session-state contamination on stateful endpoints."
        ),
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
        default=100,
        ge=1,
        description=(
            "Hard cap on the total number of scenarios executed. "
            "Applied after deduplication, preserving priority order: L1, L2, L3, L4, L5 "
            "(and, when prioritize_by_probe is enabled, demoting probed-blocked tool "
            "families within that order — see prioritize_by_probe). "
            "Set to null/None for no cap."
        ),
    )
    escalate_on_refusal: bool = Field(
        default=False,
        description=(
            "When a tool-coverage probe's response looks like a canned refusal "
            "(see nuguard.behavior.refusal.classify_refusal), retry with an "
            "escalating ladder of phrasings — explicit SBOM name/description, then "
            "(for missing-precondition refusals) a setup turn establishing context — "
            "before giving up on that tool. Default off so existing scenario/report "
            "snapshots are unaffected."
        ),
    )
    escalation_max_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        description=(
            "Max attempts (including the first, natural-phrasing attempt) per tool "
            "before giving up when escalate_on_refusal is enabled."
        ),
    )
    escalation_circuit_breaker_threshold: int = Field(
        default=3,
        ge=1,
        description=(
            "Consecutive escalation attempts across a tool family (same owning agent, "
            "or the __standalone__ group) that return the SAME refusal classification "
            "before the whole family is tagged systemic_deflection and skipped for the "
            "rest of the run. Only applies when escalate_on_refusal is enabled."
        ),
    )
    prioritize_by_probe: bool = Field(
        default=False,
        description=(
            "Run one lightweight reachability probe per agent/tool-family before "
            "building the full tool-coverage scenario set, then schedule scenarios for "
            "probed-reachable (or not-yet-probed) families ahead of probed-blocked "
            "families within max_scenarios, and record blocked-and-cut families "
            "distinctly as deprioritized rather than silently dropped. Most useful "
            "together with escalate_on_refusal (which additionally retries blocked "
            "tools within a scenario), but toggles independently."
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


class ValidateBoundaryAssertion(BaseModel):
    """Single boundary assertion declared in nuguard.yaml validate.boundary_assertions."""

    name: str
    message: str
    expect: Literal["refused"] = "refused"
    forbid_pattern: str = ""


class ValidateConfig(BaseModel):
    """Configuration for nuguard validate mode."""

    target: str = ""
    target_endpoint: str = ""
    auth: AppAuthConfig = Field(default_factory=AppAuthConfig)
    canary: str = ""
    workflows: list[str] = Field(
        default_factory=list,
        description=(
            "Workflows to execute: capability_probe, happy_path, policy_compliance, "
            "agent_routing, boundary_assertion. Empty = run all."
        ),
    )
    boundary_assertions: list[ValidateBoundaryAssertion] = Field(default_factory=list)
    request_timeout: float = 60.0
    verbose: bool = False
    chat_payload_key: str = "message"
    chat_payload_list: bool = False
    chat_payload_format: Literal["json", "form"] = "json"
    chat_response_key: str = ""

    @field_validator("auth", mode="before")
    @classmethod
    def _coerce_none_auth(cls, v: Any) -> Any:
        """Convert None (auth: with all sub-keys commented out) to an empty dict."""
        return v if v is not None else {}


class RedteamFindingTriggers(BaseModel):
    """Configurable controls for when redteam findings are emitted."""

    canary_hits: bool = True
    policy_violations: bool = True
    critical_success_hits: bool = True
    any_inject_success: bool = False
    # Phase 3 catalog evidence layers
    tool_trace_hits: bool = True

    def any_enabled(self) -> bool:
        """Return True when at least one trigger is enabled."""
        return any(
            [
                self.canary_hits,
                self.policy_violations,
                self.critical_success_hits,
                self.any_inject_success,
                self.tool_trace_hits,
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
    sbom_llm_concurrency: int | None = Field(
        default=None,
        ge=1,
        le=64,
        description=(
            "Max in-flight LLM calls during SBOM enrichment (yaml: "
            "sbom_generation.llm_concurrency, issue #197). When None, the "
            "AiSbomConfig default (5) is used."
        ),
    )
    sbom_gap_fill_max_calls: int | None = Field(
        default=None,
        description=(
            "Max LLM calls for the gap-fill discovery pass "
            "(yaml: sbom_generation.gap_fill.max_calls, default 40)."
        ),
    )
    sbom_gap_fill_max_cost_usd: float | None = Field(
        default=None,
        description=(
            "Max estimated USD spend for the gap-fill discovery pass "
            "(yaml: sbom_generation.gap_fill.max_cost_usd, default 5.0)."
        ),
    )
    sbom_gap_fill_enable_privilege: bool = Field(
        default=False,
        description=(
            "Opt into LLM gap-fill for PRIVILEGE nodes, off by default "
            "(yaml: sbom_generation.gap_fill.enable_privilege)."
        ),
    )
    sbom_gap_fill_enable_guardrail: bool = Field(
        default=False,
        description=(
            "Opt into LLM gap-fill for GUARDRAIL nodes, off by default "
            "(yaml: sbom_generation.gap_fill.enable_guardrail)."
        ),
    )
    sbom_gap_fill_self_critique_categories: list[str] = Field(
        default_factory=list,
        description=(
            "Extra component categories that get a Round-3 self-critique pass "
            "beyond the always-forced privilege/guardrail "
            "(yaml: sbom_generation.gap_fill.self_critique_categories)."
        ),
    )
    sbom_verification_max_verifications: int | None = Field(
        default=None,
        description=(
            "Max node-verification LLM calls per scan "
            "(yaml: sbom_generation.verification.max_verifications; "
            "env: AISBOM_MAX_VERIFICATIONS, default 20)."
        ),
    )
    sbom_verification_cost_budget: float | None = Field(
        default=None,
        description=(
            "Max estimated USD spend for node verification "
            "(yaml: sbom_generation.verification.cost_budget; "
            "env: AISBOM_VERIFICATION_COST_BUDGET, default 20.0)."
        ),
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
    redteam_chat_payload_extras: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extra static JSON fields merged into every redteam chat POST body "
            "(yaml: redteam.chat_payload_extras or target.chat_payload_extras). "
            "Useful for apps that require additional context fields such as vehicleState "
            "or language alongside the message. The message key always takes precedence."
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
        description=(
            "Scan profile: 'ci' (fast, ≥5 impact), 'standard' (≥3 impact, ~30 scenarios), "
            "or 'full' (all scenarios, ≥50 on rich SBOMs) (yaml: redteam.profile)."
        ),
    )
    redteam_mode: str = Field(
        default="concurrent",
        description=(
            "'concurrent' (default, existing behavior — phase-gated with intra-phase "
            "parallelism) or 'progressive' (strictly sequential named 0-12 phase "
            "engagement, see docs/claude-redteam-3.md) (yaml: redteam.mode)."
        ),
    )
    redteam_progressive_halt_on_severity: str = Field(
        default="none",
        description=(
            "Progressive-mode phase gate: 'none' (default — run every phase regardless "
            "of findings), 'high', or 'critical' (stop dispatching further phases once a "
            "finding at or above this severity is confirmed in the just-completed phase) "
            "(yaml: redteam.progressive.halt_on_severity)."
        ),
    )
    redteam_catalog_path: str | None = Field(
        default=None,
        description=(
            "Path to a custom catalog YAML file. When set, replaces the built-in "
            "scenario catalog. Generate a starting file with: "
            "nuguard redteam catalog-export (yaml: redteam.catalog_path)."
        ),
    )
    min_impact_score: float = Field(
        default=0.0,
        description="Minimum pre-score [0–10] for scenario inclusion (yaml: redteam.min_impact_score).",
    )
    redteam_scenarios: list[str] = Field(
        default_factory=list,
        description=(
            "Goal types to run; empty = all 9. Values: prompt-driven-threat, "
            "policy-violation, data-exfiltration, privilege-escalation, "
            "tool-abuse, mcp-toxic-flow, api-attack, agentic-trust-abuse, "
            "recon-inference (yaml: redteam.scenarios)."
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
    redteam_scenario_timeout: float = Field(
        default=600.0,
        description=(
            "Per-scenario wall-clock timeout in seconds (yaml: redteam.scenario_timeout). "
            "Scenarios that exceed this limit are cancelled and recorded as 'timeout'. "
            "0 disables the timeout. Default is 600 s to accommodate in-semaphore "
            "transient-retry loops for slow cold-starting targets."
        ),
    )
    redteam_similar_miss_threshold: int = Field(
        default=4,
        description=(
            "Number of misses from similar attack scenarios before subsequent similar "
            "scenarios are suppressed (yaml: redteam.similar_miss_threshold). "
            "Prevents repeating the same failing attack angle across many scenarios. "
            "Set to 0 to disable."
        ),
    )
    redteam_hard_refusal_abort_turns: int = Field(
        default=5,
        description=(
            "Number of consecutive hard/soft refusals in a guided conversation before "
            "aborting the scenario (yaml: redteam.hard_refusal_abort_turns). "
            "Increase for agents with aggressive topic guardrails."
        ),
    )
    redteam_stall_abort_threshold: int = Field(
        default=8,
        description=(
            "Number of consecutive stalled turns (progress score ≤ 2) in a guided "
            "conversation before aborting the scenario "
            "(yaml: redteam.stall_abort_threshold). "
            "Increase to give the attacker more turns when the agent gives partial responses."
        ),
    )
    redteam_skip_discovery: bool = Field(
        default=False,
        description=(
            "Skip the pre-scan discovery conversation (yaml: redteam.skip_discovery). "
            "When True, NuGuard does not send warm-up turns to the target agent before "
            "generating attack payloads. Useful for air-gapped or highly sensitive "
            "target environments."
        ),
    )
    redteam_discovery_max_turns: int = Field(
        default=3,
        description=(
            "Maximum turns to send during pre-scan discovery (yaml: redteam.discovery_max_turns). "
            "Discovery stops early when a name or ID is extracted."
        ),
    )
    redteam_capability_discovery: bool = Field(
        default=True,
        description=(
            "Probe the live agent for tools, sub-agents, and its system prompt when the "
            "AI-SBOM is missing them, and merge the findings back into the in-memory SBOM "
            "before scenario generation (yaml: redteam.capability_discovery). Only fires "
            "for AGENT nodes with an actual gap, so a well-populated SBOM sends no extra "
            "turns. Findings are tagged with confidence 0.5 and evidence kind "
            "'dynamic_probe' to distinguish them from static-analysis results."
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
    redteam_concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "Maximum number of redteam scenarios to run in parallel "
            "(yaml: redteam.concurrency). Lower this for rate-limited targets."
        ),
    )
    redteam_turn_delay_seconds: float = Field(
        default=5.0,
        ge=0.0,
        description=(
            "Inter-turn pause in seconds to avoid 429s on the target "
            "(yaml: redteam.turn_delay_seconds)."
        ),
    )
    redteam_codegen_escalation_enabled: bool = Field(
        default=True,
        description=(
            "When True, a confirmed code-generation success automatically spawns an "
            "escalation chain probing safeguard removal, data exfiltration, and tool abuse "
            "(yaml: redteam.codegen_escalation_enabled)."
        ),
    )
    redteam_max_concurrent_requests: int = Field(
        default=1,
        ge=0,
        description=(
            "Maximum concurrent HTTP requests to the target across all parallel "
            "scenarios (yaml: redteam.max_concurrent_requests). "
            "0 = unlimited; 1 = fully serialised (safest for low-quota Azure apps)."
        ),
    )
    redteam_max_transient_hold_seconds: float = Field(
        default=300.0,
        ge=0.0,
        description=(
            "Maximum total seconds to hold the HTTP semaphore while retrying transient "
            "errors on the first request of a new chain. After this limit the semaphore "
            "is released so other chains can proceed "
            "(yaml: redteam.max_transient_hold_seconds)."
        ),
    )
    redteam_scenario_delay_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Pause between scenario starts in seconds to avoid 429s "
            "(yaml: redteam.scenario_delay_seconds)."
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
    redteam_pre_run_warmup: int = Field(
        default=0,
        ge=0,
        le=10,
        description=(
            "Number of lightweight warmup requests to send to the target before "
            "scenarios start (yaml: redteam.pre_run_warmup). "
            "Use 1–3 for Azure Container Apps or other serverless targets that "
            "scale-to-zero between runs — wakes the container before scenarios fire."
        ),
    )
    redteam_verify_findings: bool = Field(
        default=True,
        description=(
            "Re-probe the target with the exact successful payload after a finding is "
            "emitted to confirm it reproduces (yaml: redteam.verify_findings). "
            "On by default — one extra request per confirmed finding, not per scenario. "
            "The probe reuses the judge that produced the original finding and feeds the "
            "result into the finding's NGRS likelihood score, in addition to the "
            "verified/unconfirmed badge in the report. Set to false to skip it."
        ),
    )
    redteam_suppress_spa_html_auth_bypass: bool = Field(
        default=True,
        description=(
            "Suppress HTTP-2xx auth-bypass findings when the response body is an SPA "
            "HTML shell (yaml: redteam.suppress_spa_html_auth_bypass). "
            "Single-page applications served by a catch-all web server return 200 + HTML "
            "for every path, so a 2xx on a non-API path is not a real auth-bypass. "
            "Set to false to report all 2xx responses as findings."
        ),
    )
    redteam_golden_data: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Static pre-seed for the golden-data cache, keyed by SBOM agent node label "
            "(yaml: redteam.golden_data). "
            "Supported subkeys: account_id, name, email. "
            "Eliminates the live DISCOVER step for known test accounts, making token "
            "substitution ({golden_id} etc.) reliable on slow or auth-gated targets."
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
    analyze_nga_only: bool = Field(
        default=False,
        description=(
            "Run only NGA structural rules (NGA-001–018), skipping external tool "
            "scans (yaml: analyze.nga_only, CLI: --nga)."
        ),
    )
    analyze_supply_chain_profile: str = Field(
        default="standard",
        description=(
            "Supply-chain scan profile: ci | standard | full "
            "(yaml: analyze.supply_chain_profile, CLI: --supply-chain-profile)."
        ),
    )
    analyze_supply_chain_verify: str = Field(
        default="off",
        description=(
            "Artifact registry verification mode: off | warn | fail "
            "(yaml: analyze.supply_chain_verify, CLI: --supply-chain-verify)."
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

    # ------------------------------------------------------- Validate mode
    validate_config: ValidateConfig = Field(
        default_factory=ValidateConfig,
        description="Configuration for nuguard validate mode.",
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

    def resolved_validate_auth_config(self) -> "AuthConfig":
        """Build an AuthConfig from the resolved *validate* auth settings."""
        from nuguard.common.auth import AuthConfig  # noqa: PLC0415

        vc_auth = self.validate_config.auth
        if vc_auth.type and vc_auth.type != "none":
            return AuthConfig(
                type=vc_auth.type,  # type: ignore[arg-type]
                header=vc_auth.header or "",
                username=vc_auth.username,
                password=vc_auth.password,
                login_flow=vc_auth.login_flow,
            )
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

    # A YAML key written with no value (e.g. "workflows:") parses to None, not
    # an empty list/string — that means "leave this at its default", not
    # "explicitly set to null". Drop such top-level keys so field defaults
    # apply instead of failing type validation (e.g. list[str] fields).
    yaml_overrides = {k: v for k, v in yaml_overrides.items() if v is not None}

    try:
        return NuGuardConfig(**yaml_overrides)
    except ValidationError as exc:
        field_issues = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(
            f"Invalid nuguard.yaml configuration — {field_issues}. "
            "Check the field(s) above against nuguard.yaml.example, or remove "
            "them to use their default value."
        ) from exc
