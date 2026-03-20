"""YAML-based adapters for AI SBOM extraction.

Parses structured YAML configuration files used by AI frameworks.

Supported patterns
------------------
``CrewAIYAMLAdapter``:
    Detects agents defined in CrewAI ``config/agents.yaml`` files.

``AutoGenYAMLAdapter``:
    Detects agent configs from AutoGen-style YAML files.

``LLMYAMLConfigAdapter``:
    Detects models and providers from generic LLM config YAML files.
    Matches ``llm.yaml``, ``llm_config.yaml``, ``providers.yaml``, etc.
    Parses a ``providers:`` block to emit MODEL + FRAMEWORK nodes.

``PromptFileAdapter``:
    Detects prompt template files.  Triggered by file-path pattern
    (files inside a ``prompts/`` directory with ``.txt`` extension,
    or files named ``*_prompt.txt`` / ``*_system.txt``).
    Emits one PROMPT node per file with a content preview.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from xelo.adapters.base import ComponentDetection, RelationshipHint
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_load_yaml(content: str) -> Any:
    """Parse YAML content, returning None on failure."""
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(content)
    except Exception as exc:  # noqa: BLE001
        _log.debug("YAML parse error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# CrewAI agents.yaml adapter
# ---------------------------------------------------------------------------


class CrewAIYAMLAdapter:
    """Detect CrewAI agents defined in YAML configuration files.

    CrewAI projects store agent definitions in ``config/agents.yaml``.
    The file is a mapping where each top-level key is the agent variable name
    and the value is a dict with at least ``role`` and/or ``goal`` fields.

    Example::

        researcher:
          role: Senior Research Analyst
          goal: Uncover cutting-edge developments in AI
          backstory: ...

    Matching heuristic: the path must contain ``agents.yaml`` (case-insensitive)
    and the parsed value must be a mapping of non-empty dicts that contain at
    least one of ``role``, ``goal``, or ``backstory``.
    """

    name = "crewai_yaml"
    priority = 35  # lower than python adapters but higher than regex-only

    #: Path fragment that must be present for this adapter to fire
    _PATH_PATTERN = re.compile(r"agents\.ya?ml$", re.IGNORECASE)

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        """Return AGENT detections for each agent defined in a CrewAI YAML file.

        Parameters
        ----------
        content:
            Raw file text.
        rel_path:
            Path relative to the repo root (for evidence location).
        """
        path_str = str(rel_path)
        if not self._PATH_PATTERN.search(path_str):
            return []

        data = _try_load_yaml(content)
        if not isinstance(data, dict):
            return []

        detections: list[ComponentDetection] = []
        line_cache = _build_line_index(content)

        for agent_key, agent_val in data.items():
            if not isinstance(agent_val, dict):
                continue
            # Must have at least one of these canonical CrewAI agent fields
            if not any(k in agent_val for k in ("role", "goal", "backstory")):
                continue

            agent_name = str(agent_key).strip()
            if not agent_name:
                continue

            role = (agent_val.get("role") or "").strip()
            goal = (agent_val.get("goal") or "").strip()
            line = _find_key_line(line_cache, agent_name)

            det = ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=agent_name,
                display_name=agent_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.85,
                metadata={
                    "framework": "crewai",
                    "role": role or None,
                    "goal": goal or None,
                    "source": "yaml_config",
                },
                file_path=rel_path,
                line=line,
                snippet=f"{agent_name}: role={role[:60]!r}" if role else agent_name,
                evidence_kind="yaml",
            )
            detections.append(det)
            _log.debug("crewai_yaml: detected agent %r in %s (line %s)", agent_name, rel_path, line)

        return detections


# ---------------------------------------------------------------------------
# AutoGen config.yaml adapter
# ---------------------------------------------------------------------------

_AUTOGEN_PATH_RE = re.compile(
    r"(autogen|OAI_CONFIG|model_config).*\.ya?ml$"
    r"|config\.ya?ml$",  # generic config.yaml files may use autogen format
    re.IGNORECASE,
)
_AUTOGEN_MODEL_FIELDS = {"model", "engine", "api_engine"}

# Autogen provider prefix (identifies autogen-ext model config files)
_AUTOGEN_PROVIDER_PREFIX = "autogen_ext"
# Agent keys that indicate an AutoGen distributed chat agent definition
_AUTOGEN_AGENT_KEYS = {"description", "system_message", "human_input_mode", "is_termination_msg"}


class AutoGenYAMLAdapter:
    """Detect models and agents from AutoGen YAML configuration files.

    Handles two config varieties:

    1. **Model config** (``model_config.yaml`` / ``config.yaml`` with
       ``provider: autogen_ext.models.*``):
       Detects ``model`` entries inside ``config:`` blocks.

    2. **Agent config** (``config.yaml`` with top-level agent keys):
       Detects agents that have ``description`` and/or ``system_message``
       sub-keys — the AutoGen distributed group chat pattern.
    """

    name = "autogen_yaml"
    priority = 36

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        path_str = str(rel_path)
        if not _AUTOGEN_PATH_RE.search(path_str):
            return []

        data = _try_load_yaml(content)
        if not isinstance(data, dict):
            return []

        detections: list[ComponentDetection] = []
        line_cache = _build_line_index(content)

        # Pattern 1: AutoGen model config with provider + config.model
        if self._is_autogen_model_config(data):
            config_block = data.get("config") or {}
            if isinstance(config_block, dict):
                for field in _AUTOGEN_MODEL_FIELDS:
                    model = (config_block.get(field) or "").strip()
                    if model:
                        line = _find_key_line(line_cache, model)
                        detections.append(
                            ComponentDetection(
                                component_type=ComponentType.MODEL,
                                canonical_name=model.lower(),
                                display_name=model,
                                adapter_name=self.name,
                                priority=self.priority,
                                confidence=0.85,
                                metadata={"framework": "autogen", "source": "yaml_config"},
                                file_path=rel_path,
                                line=line,
                                snippet=f"model: {model}",
                                evidence_kind="yaml",
                            )
                        )
            # Check model_config sub-block too
            mc = data.get("model_config") or {}
            if isinstance(mc, dict):
                cfg = mc.get("config") or {}
                if isinstance(cfg, dict):
                    model = (cfg.get("model") or "").strip()
                    if model:
                        line = _find_key_line(line_cache, model)
                        detections.append(
                            ComponentDetection(
                                component_type=ComponentType.MODEL,
                                canonical_name=model.lower(),
                                display_name=model,
                                adapter_name=self.name,
                                priority=self.priority,
                                confidence=0.85,
                                metadata={"framework": "autogen", "source": "yaml_config"},
                                file_path=rel_path,
                                line=line,
                                snippet=f"model: {model}",
                                evidence_kind="yaml",
                            )
                        )

        # Pattern 2: OAI_CONFIG_LIST style (list of dicts with model field)
        for key in ("config_list", "models"):
            sub = data.get(key)
            if isinstance(sub, list):
                seen: set[str] = set()
                for entry in sub:
                    if isinstance(entry, dict):
                        for field in _AUTOGEN_MODEL_FIELDS:
                            model = (entry.get(field) or "").strip()
                            if model and model not in seen:
                                seen.add(model)
                                line = _find_key_line(line_cache, model)
                                detections.append(
                                    ComponentDetection(
                                        component_type=ComponentType.MODEL,
                                        canonical_name=model.lower(),
                                        display_name=model,
                                        adapter_name=self.name,
                                        priority=self.priority,
                                        confidence=0.80,
                                        metadata={"framework": "autogen", "source": "yaml_config"},
                                        file_path=rel_path,
                                        line=line,
                                        snippet=f"model: {model}",
                                        evidence_kind="yaml",
                                    )
                                )

        # Pattern 3: AutoGen distributed chat agents (top-level keys with
        # description + system_message sub-keys)
        for key, val in data.items():
            if not isinstance(val, dict):
                continue
            if not any(k in val for k in _AUTOGEN_AGENT_KEYS):
                continue
            # Skip non-agent keys (host, group_chat_manager, client_config, etc.)
            if key in {
                "host",
                "client_config",
                "group_chat_manager",
                "model_config",
                "config_list",
                "models",
                "host",
                "ui_agent",
            }:
                continue
            agent_name = str(key).strip()
            if not agent_name:
                continue
            description = (val.get("description") or "").strip()
            line = _find_key_line(line_cache, agent_name)
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=agent_name,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.80,
                    metadata={
                        "framework": "autogen",
                        "description": description[:100] if description else None,
                        "source": "yaml_config",
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=f"{agent_name}: description={description[:60]!r}"
                    if description
                    else agent_name,
                    evidence_kind="yaml",
                )
            )
            _log.debug("autogen_yaml: agent %r in %s", agent_name, rel_path)

        return detections

    def _is_autogen_model_config(self, data: dict[str, Any]) -> bool:
        """Check if the YAML looks like an AutoGen model config."""
        provider = str(data.get("provider") or "")
        if _AUTOGEN_PROVIDER_PREFIX in provider:
            return True
        mc = data.get("model_config") or {}
        if isinstance(mc, dict):
            provider2 = str(mc.get("provider") or "")
            if _AUTOGEN_PROVIDER_PREFIX in provider2:
                return True
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_line_index(content: str) -> list[str]:
    """Split content into lines (1-indexed via list[0] = line 1)."""
    return ["<dummy>"] + content.splitlines()


def _find_key_line(line_cache: list[str], key: str) -> int:
    """Return 1-based line number where ``key:`` first appears, or 1."""
    # Quick linear scan — YAML config files are small
    prefix = key + ":"
    for i, line in enumerate(line_cache[1:], start=1):
        stripped = line.strip()
        if stripped.startswith(prefix) or stripped == key + ":":
            return i
    return 1


# ---------------------------------------------------------------------------
# LLM YAML config adapter
# ---------------------------------------------------------------------------

_LLM_YAML_PATH_RE = re.compile(
    r"(?:^|/)(?:llm[_-]?config.*|providers.*|models.*|config/llm)\.ya?ml$",
    re.IGNORECASE,
)

# Known base_url substrings → (provider_name, display_name)
_LLM_YAML_BASE_URL_PROVIDERS: list[tuple[str, str]] = [
    ("api.groq.com", "groq"),
    ("generativelanguage.googleapis.com", "google"),
    ("aiplatform.googleapis.com", "google"),
    ("localhost:11434", "ollama"),
    ("127.0.0.1:11434", "ollama"),
    ("api.anthropic.com", "anthropic"),
    ("api.mistral.ai", "mistral"),
    ("api.together.xyz", "togetherai"),
    ("api.deepseek.com", "deepseek"),
    ("openrouter.ai", "openrouter"),
    ("api.openai.com", "openai"),
]


def _provider_from_base_url(base_url: str) -> str | None:
    url = (base_url or "").lower()
    for substring, provider in _LLM_YAML_BASE_URL_PROVIDERS:
        if substring in url:
            return provider
    return None


class LLMYAMLConfigAdapter:
    """Detect LLM models and providers from generic YAML config files.

    Matches files whose name matches ``llm[_-]?config*.yaml``,
    ``providers*.yaml``, ``models*.yaml``, or ``config/llm.yaml``.

    Parses a ``providers:`` mapping block where each key is a provider entry
    containing ``model`` and optionally ``base_url`` fields::

        providers:
          groq:
            model: llama-3.3-70b-versatile
            base_url: https://api.groq.com/openai/v1
            enabled: true
          ollama:
            model: llama3.2:3b
            base_url: http://localhost:11434/v1

    Emits one MODEL node per provider entry that has a ``model`` value, and one
    FRAMEWORK node per resolved provider (via ``base_url`` or key name).
    """

    name = "llm_yaml_config"
    priority = 37

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        if not _LLM_YAML_PATH_RE.search(rel_path):
            return []

        data = _try_load_yaml(content)
        if not isinstance(data, dict):
            _log.debug("llm_yaml_config: %s is not a YAML mapping — skipping", rel_path)
            return []

        detections: list[ComponentDetection] = []
        line_cache = _build_line_index(content)

        # Support top-level ``providers:`` block or inline list at root
        providers_block = data.get("providers") or data.get("llm_providers") or data.get("models")
        if not isinstance(providers_block, dict):
            _log.debug("llm_yaml_config: no recognizable providers block in %s", rel_path)
            return []

        seen_providers: set[str] = set()

        for entry_key, entry_val in providers_block.items():
            if not isinstance(entry_val, dict):
                continue

            # Skip explicitly disabled entries
            enabled = entry_val.get("enabled")
            if enabled is False:
                _log.debug(
                    "llm_yaml_config: skipping disabled provider %r in %s", entry_key, rel_path
                )
                continue

            model = str(entry_val.get("model") or "").strip()
            base_url = str(entry_val.get("base_url") or entry_val.get("api_base") or "").strip()

            # Resolve provider: try base_url first, then entry key name
            provider = _provider_from_base_url(base_url) or str(entry_key).lower().strip()

            # Use the provider entry key's line for all detections within this entry.
            # Model names don't appear as top-level YAML keys so looking them up would
            # always fall back to line 1, causing location-based dedup to collapse
            # multiple provider entries into one.
            entry_line = _find_key_line(line_cache, entry_key)

            if model:
                _fw_canon = canonicalize_text(f"framework:{provider}")
                _model_canon = canonicalize_text(model.lower())
                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.MODEL,
                        canonical_name=model.lower(),
                        display_name=model,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "framework": provider,
                            "provider": provider,
                            "source": "yaml_config",
                            "base_url": base_url or None,
                        },
                        file_path=rel_path,
                        line=entry_line,
                        snippet=f"{entry_key}: model={model}",
                        evidence_kind="yaml",
                        relationships=[
                            RelationshipHint(
                                source_canonical=_fw_canon,
                                source_type=ComponentType.FRAMEWORK,
                                target_canonical=_model_canon,
                                target_type=ComponentType.MODEL,
                                relationship_type="USES",
                            )
                        ],
                    )
                )
                _log.debug(
                    "llm_yaml_config: model=%r provider=%r in %s (line=%d)",
                    model,
                    provider,
                    rel_path,
                    entry_line,
                )

            # Emit one FRAMEWORK node per unique provider
            if provider not in seen_providers:
                seen_providers.add(provider)
                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.FRAMEWORK,
                        canonical_name=f"framework:{provider}",
                        display_name=provider,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.80,
                        metadata={
                            "framework": provider,
                            "source": "yaml_config",
                            "base_url": base_url or None,
                        },
                        file_path=rel_path,
                        line=entry_line,
                        snippet=f"{entry_key}: base_url={base_url}" if base_url else entry_key,
                        evidence_kind="yaml",
                    )
                )

        _log.info("llm_yaml_config: %d detections in %s", len(detections), rel_path)
        return detections


# ---------------------------------------------------------------------------
# Prompt file adapter
# ---------------------------------------------------------------------------

_PROMPT_DIR_RE = re.compile(r"(?:^|[\\/])prompts?[\\/]", re.IGNORECASE)
_PROMPT_FILENAME_RE = re.compile(
    r"(?:system[_-]|user[_-]|assistant[_-]|[_-]prompt|[_-]template|[_-]system)"
    r".*\.txt$",
    re.IGNORECASE,
)
# Detects {placeholder} / {{placeholder}} template variable syntax
_TEMPLATE_VAR_RE = re.compile(r"\{[\w_]+\}")


class PromptFileAdapter:
    """Detect prompt template files by file-path pattern.

    Triggered for:
    - Any ``.txt`` file inside a directory named ``prompts/`` or ``prompt/``
    - Files named ``*_prompt.txt``, ``*_system.txt``, ``system_*.txt``,
      ``*_template.txt``

    Emits one PROMPT node per file.  ``is_template`` is True when ``{variable}``
    placeholders are found.

    This adapter is called from the extractor *before* the docs-tier skip so
    that ``.txt`` files in prompt directories are not silently ignored.
    """

    name = "prompt_file"
    priority = 45

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        """Return a single PROMPT detection if *rel_path* looks like a prompt file."""
        path_str = rel_path.replace("\\", "/")

        is_in_prompts_dir = bool(_PROMPT_DIR_RE.search(path_str))
        is_prompt_filename = bool(_PROMPT_FILENAME_RE.search(Path(rel_path).name))

        if not (is_in_prompts_dir or is_prompt_filename):
            return []

        stripped = content.strip()
        if not stripped:
            _log.debug("prompt_file: skipping empty file %s", rel_path)
            return []

        preview = stripped[:160].replace("\n", " ")
        is_template = bool(_TEMPLATE_VAR_RE.search(stripped))
        template_vars = list({m.group(0) for m in _TEMPLATE_VAR_RE.finditer(stripped)})

        # Use the filename stem as the display name (title-case)
        stem = Path(rel_path).stem.replace("_", " ").replace("-", " ").title()

        _log.debug(
            "prompt_file: detected prompt %r in %s (is_template=%s)",
            stem,
            rel_path,
            is_template,
        )
        return [
            ComponentDetection(
                component_type=ComponentType.PROMPT,
                canonical_name=canonicalize_text(stem),
                display_name=stem,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.75,
                metadata={
                    "source": "prompt_file",
                    "content": stripped,
                    "is_template": is_template,
                    "template_variables": template_vars,
                    "char_count": len(stripped),
                    "role": "system" if "system" in rel_path.lower() else "user",
                },
                file_path=rel_path,
                line=1,
                snippet=preview[:80],
                evidence_kind="prompt_file",
            )
        ]
