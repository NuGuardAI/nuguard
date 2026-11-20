"""Remote scan service configuration (``service:`` section of ``nuguard.yaml``).

Values support ``${ENV}`` / ``${ENV:-default}`` interpolation like the rest of
``nuguard.yaml``. ``NUGUARD_SERVICE_*`` environment variables override the
file, using ``__`` for nesting (``NUGUARD_SERVICE_JOBS__MAX_CONCURRENT=2``).
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from nuguard.models.finding import Severity

ALL_TOOLS: tuple[str, ...] = (
    "nga-rules", "atlas", "semgrep", "osv", "grype", "checkov", "trivy", "supply-chain",
)

# Environment variables a worker may inherit when listed in jobs.passthrough_env.
# Credentials are never passed through, even if an operator lists them.
_NEVER_PASSTHROUGH_PREFIXES = ("NUGUARD_", "AISBOM_", "LITELLM_")
_NEVER_PASSTHROUGH = {"GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                      "GEMINI_API_KEY", "GOOGLE_CLOUD_API_KEY", "AWS_SECRET_ACCESS_KEY",
                      "AWS_SESSION_TOKEN"}


class ServiceConfigError(ValueError):
    """The service configuration is not safe or not complete enough to start."""


class ApiKeyConfig(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    sha256: str = Field(description="Hex SHA-256 of the API key; plaintext is never stored")
    max_concurrent_scans: int = Field(default=4, ge=1)
    max_queued_scans: int = Field(default=50, ge=1)

    @field_validator("sha256")
    @classmethod
    def _hex(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("sha256 must be a 64-character hex digest")
        return value


class GitSettings(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=lambda: ["github.com"])
    clone_timeout_s: float = Field(default=120.0, gt=0)
    max_repo_bytes: int = Field(default=500 * 1024 * 1024, ge=1)


class JobsSettings(BaseModel):
    max_concurrent: int = 4
    job_timeout_s: float = Field(default=900.0, gt=0)
    queue_limit: int = Field(default=200, ge=1)
    result_ttl_hours: float = Field(default=168.0, gt=0)
    scratch_dir: Path = Path("/var/lib/nuguard/scratch")
    db_path: Path = Path("/var/lib/nuguard/service.db")
    # Results larger than this are stored as files beside the DB.
    inline_result_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    # Linux-only resource limits applied inside each worker. RLIMIT_AS is off by
    # default: it caps *virtual* memory, and Semgrep's engine (and Go scanners)
    # reserve far more address space than they use, so a 4 GB cap makes them fail.
    # Bound real memory with the container's memory limit (cgroups) instead.
    rlimit_as_bytes: int | None = Field(default=None, ge=256 * 1024**2)
    rlimit_fsize_bytes: int = Field(default=1024**3, ge=1024**2)
    rlimit_nproc: int | None = Field(default=None, ge=16)
    # Extra variables workers may inherit (e.g. GRYPE_DB_CACHE_DIR, HTTPS_PROXY).
    passthrough_env: list[str] = Field(
        default_factory=lambda: [
            "GRYPE_DB_CACHE_DIR", "GRYPE_DB_AUTO_UPDATE", "GRYPE_CHECK_FOR_APP_UPDATE",
            "TRIVY_CACHE_DIR", "TRIVY_SKIP_DB_UPDATE", "TRIVY_SKIP_JAVA_DB_UPDATE",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
        ]
    )

    @field_validator("passthrough_env")
    @classmethod
    def _no_credentials(cls, value: list[str]) -> list[str]:
        return [
            name for name in value
            if name not in _NEVER_PASSTHROUGH and not name.startswith(_NEVER_PASSTHROUGH_PREFIXES)
        ]


class AnalysisDefaults(BaseModel):
    min_severity: Severity = Severity.LOW
    network: bool = True
    tools: list[str] = Field(default_factory=lambda: list(ALL_TOOLS))

    @field_validator("tools")
    @classmethod
    def _known_tools(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(ALL_TOOLS))
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(unknown)}")
        return [t for t in ALL_TOOLS if t in value]


class LlmSettings(BaseModel):
    enabled: bool = False
    model: str | None = None
    api_key: SecretStr | None = None
    api_base: str | None = None


class WebhookSettings(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=list)
    secret: SecretStr | None = None
    # Hosts that may resolve to private/loopback addresses (e.g. an in-cluster receiver).
    allow_private_hosts: list[str] = Field(default_factory=list)


class TlsSettings(BaseModel):
    cert_file: Path | None = None
    key_file: Path | None = None


class ServiceSettings(BaseSettings):
    """Validated ``service:`` configuration."""

    model_config = SettingsConfigDict(
        env_prefix="NUGUARD_SERVICE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    bind: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    public_base_url: str | None = None
    trust_proxy: bool = False
    metrics: bool = False
    api_keys: list[ApiKeyConfig] = Field(default_factory=list)
    git: GitSettings = Field(default_factory=GitSettings)
    jobs: JobsSettings = Field(default_factory=JobsSettings)
    analysis_defaults: AnalysisDefaults = Field(default_factory=AnalysisDefaults)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    webhooks: WebhookSettings = Field(default_factory=WebhookSettings)
    tls: TlsSettings = Field(default_factory=TlsSettings)
    # Failed authentications allowed per remote address per minute.
    auth_fail_limit_per_minute: int = Field(default=10, ge=1)
    max_body_bytes: int = Field(default=16 * 1024, ge=1024)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment overrides the YAML file (passed as init kwargs).
        return (env_settings, init_settings)

    def startup_problems(self) -> list[str]:
        """Reasons the service must refuse to start (empty when it is safe)."""
        problems: list[str] = []
        if not self.api_keys:
            problems.append("service.api_keys is empty: no caller could authenticate")
        if len({k.id for k in self.api_keys}) != len(self.api_keys):
            problems.append("service.api_keys ids must be unique")
        if not self.git.allowed_hosts:
            problems.append("service.git.allowed_hosts is empty: every repository would be rejected")
        if self.jobs.max_concurrent < 1:
            problems.append("service.jobs.max_concurrent must be at least 1")
        has_tls = self.tls.cert_file is not None and self.tls.key_file is not None
        if not _is_loopback(self.bind) and not (has_tls or self.trust_proxy):
            problems.append(
                f"service.bind={self.bind!r} is not loopback but neither service.tls "
                "(cert_file + key_file) nor service.trust_proxy is configured; API keys and "
                "GitHub tokens would travel in clear text"
            )
        if self.llm.enabled and not self.llm.model:
            problems.append("service.llm.enabled requires service.llm.model")
        if self.webhooks.allowed_hosts and self.webhooks.secret is None:
            problems.append("service.webhooks.allowed_hosts requires service.webhooks.secret")
        return problems

    def validate_for_startup(self) -> None:
        problems = self.startup_problems()
        if problems:
            raise ServiceConfigError("; ".join(problems))


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def load_service_settings(config_file: Path | None = None) -> ServiceSettings:
    """Read ``service:`` from *config_file* (default ``./nuguard.yaml`` if present)."""
    from nuguard.config import _expand_env_vars  # noqa: PLC0415

    section: dict[str, Any] = {}
    path = config_file or Path("nuguard.yaml")
    if config_file is not None and not path.exists():
        raise ServiceConfigError(f"Config file not found: {config_file}")
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ServiceConfigError(f"Invalid YAML in {path}") from exc
        raw = data.get("service") or {} if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            raise ServiceConfigError("service: must be a mapping")
        section = _drop_none(_expand_env_vars(raw))
    return ServiceSettings(**section)


def _drop_none(value: Any) -> Any:
    """Remove keys whose value resolved to None (unset ${VAR}) so defaults apply."""
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value if v is not None]
    return value
