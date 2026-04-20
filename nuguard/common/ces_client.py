"""Google Customer Engagement Suite (CES) client for NuGuard.

Provides authentication helpers, a deployment config dataclass, and an HTTP
client for calling CES ``runSession`` endpoints.
"""
from __future__ import annotations

import logging
import os
import random
import re
import string
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

try:
    import google.auth
    import google.auth.transport.requests

    _GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    _GOOGLE_AUTH_AVAILABLE = False

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema constants (embedded in SBOM metadata)
# ---------------------------------------------------------------------------

CES_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "config": {
            "type": "object",
            "properties": {
                "session": {"type": "string"},
                "app_version": {"type": "string"},
                "deployment": {"type": "string"},
            },
        },
        "inputs": {
            "type": "array",
            "items": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    },
}

CES_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "outputs": {
            "type": "array",
            "items": {"type": "object", "properties": {"text": {"type": "string"}}},
        }
    },
}

CES_RESPONSE_TEXT_KEY = "outputs[0].text"
CES_FRAMEWORK_NAME = "google-ces"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CESAuthError(RuntimeError):
    """Raised when Google Cloud authentication fails for CES calls."""


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

_GCLOUD_AUTH_HELP = (
    "Google Cloud authentication required. Run one of:\n"
    "  gcloud auth login                          # personal account\n"
    "  gcloud auth application-default login      # application default credentials\n"
    "  gcloud auth activate-service-account ...   # service account key\n"
    "Then retry nuguard."
)

_TOKEN_LOCK = threading.Lock()
_CACHED_TOKEN: str | None = None
_CACHED_TOKEN_EXPIRY_TS: float = 0.0
_TOKEN_CACHE_TTL_SECONDS = 45 * 60
_TOKEN_REFRESH_SKEW_SECONDS = 60


def _cached_token_valid() -> bool:
    return (
        bool(_CACHED_TOKEN)
        and _CACHED_TOKEN_EXPIRY_TS > (time.time() + _TOKEN_REFRESH_SKEW_SECONDS)
    )


def _cache_token(token: str) -> str:
    global _CACHED_TOKEN, _CACHED_TOKEN_EXPIRY_TS
    _CACHED_TOKEN = token
    _CACHED_TOKEN_EXPIRY_TS = time.time() + _TOKEN_CACHE_TTL_SECONDS
    return token


def _get_adc_token() -> str | None:
    if not _GOOGLE_AUTH_AVAILABLE:
        return None
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        req = google.auth.transport.requests.Request()
        credentials.refresh(req)
        token = getattr(credentials, "token", None)
        if token:
            return token
    except Exception:
        return None
    return None


def get_gcloud_token() -> str:
    """Return a Bearer token from gcloud.

    Tries ``gcloud auth print-access-token`` first, then
    ``gcloud auth application-default print-access-token``.

    Raises:
        CESAuthError: When both attempts fail.
    """
    if _cached_token_valid():
        return _CACHED_TOKEN or ""

    with _TOKEN_LOCK:
        if _cached_token_valid():
            return _CACHED_TOKEN or ""

        env_token = os.getenv("GCP_ACCESS_TOKEN") or os.getenv("GOOGLE_ACCESS_TOKEN")
        if env_token and env_token.strip():
            return _cache_token(env_token.strip())

        adc_token = _get_adc_token()
        if adc_token:
            return _cache_token(adc_token)

    commands = [
        ["gcloud", "auth", "application-default", "print-access-token"],
    ]
    last_error: str = ""
    for cmd in commands:
        try:
            cmd_env = os.environ.copy()
            cmd_env.setdefault("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                env=cmd_env,
            )
            if result.returncode == 0:
                token = result.stdout.strip()
                if token:
                    _log.debug("gcloud token obtained via: %s", " ".join(cmd))
                    with _TOKEN_LOCK:
                        return _cache_token(token)
            last_error = result.stderr.strip() or result.stdout.strip()
        except FileNotFoundError:
            last_error = "gcloud CLI not found"
        except subprocess.TimeoutExpired:
            last_error = "gcloud timed out"
        except Exception as exc:
            last_error = str(exc)

    _log.debug("gcloud token retrieval failed: %s", last_error)
    raise CESAuthError(_GCLOUD_AUTH_HELP) from None


# ---------------------------------------------------------------------------
# Deployment config
# ---------------------------------------------------------------------------

_RESOURCE_RE = re.compile(
    r"projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)"
    r"/apps/(?P<app_id>[^/]+)/deployments/(?P<deployment_id>[^/\s\"'<>]+)"
)


@dataclass
class CESDeploymentConfig:
    """Configuration for a single CES app deployment."""

    project: str
    location: str
    app_id: str
    version_id: str
    deployment_id: str

    # Computed in __post_init__
    ces_base_url: str = field(init=False)
    app_resource: str = field(init=False)

    def __post_init__(self) -> None:
        self.app_resource = (
            f"projects/{self.project}/locations/{self.location}/apps/{self.app_id}"
        )
        self.ces_base_url = f"https://ces.googleapis.com/v1beta/{self.app_resource}"

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def from_deployment_name(cls, deployment_name: str) -> "CESDeploymentConfig":
        """Parse a CES deployment resource name and return a config instance.

        Args:
            deployment_name: Resource name of the form
                ``projects/{p}/locations/{l}/apps/{a}/deployments/{d}``.

        Returns:
            A :class:`CESDeploymentConfig` with ``version_id`` set to ``""``.

        Raises:
            ValueError: When the resource name does not match the expected pattern.
        """
        match = _RESOURCE_RE.search(deployment_name)
        if not match:
            raise ValueError(
                f"Cannot parse CES deployment resource name: {deployment_name!r}"
            )
        return cls(
            project=match.group("project"),
            location=match.group("location"),
            app_id=match.group("app_id"),
            version_id="",
            deployment_id=match.group("deployment_id"),
        )

    # ------------------------------------------------------------------
    # URL / resource helpers
    # ------------------------------------------------------------------

    def run_session_url(self, session_id: str) -> str:
        """Return the full runSession URL for the given session."""
        return f"{self.ces_base_url}/sessions/{session_id}:runSession"

    def session_resource(self, session_id: str) -> str:
        """Return the session resource name."""
        return f"{self.app_resource}/sessions/{session_id}"

    def version_resource(self) -> str:
        """Return the app version resource name."""
        return f"{self.app_resource}/versions/{self.version_id}"

    def deployment_resource(self) -> str:
        """Return the deployment resource name."""
        return f"{self.app_resource}/deployments/{self.deployment_id}"


# ---------------------------------------------------------------------------
# CES HTTP client
# ---------------------------------------------------------------------------


class CESClient:
    """Thin HTTP client for Google CES ``runSession`` calls.

    Args:
        config: Deployment configuration.
        token_fn: Callable that returns a Bearer token string.  Defaults to
            :func:`get_gcloud_token`.
    """

    def __init__(
        self,
        config: CESDeploymentConfig,
        token_fn: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._token_fn: Callable[[], str] = token_fn or get_gcloud_token

    # ------------------------------------------------------------------

    def run_turn(self, session_id: str, message: str) -> str:
        """Send one message to CES and return the reply text.

        Args:
            session_id: Active session identifier.
            message: User message text.

        Returns:
            The agent's reply text, or an empty string when no output.

        Raises:
            CESAuthError: On HTTP 401 or 403.
            httpx.HTTPError: On other HTTP errors.
        """
        token = self._token_fn()
        url = self._config.run_session_url(session_id)
        body = {
            "config": {
                "session": self._config.session_resource(session_id),
                "app_version": self._config.version_resource(),
                "deployment": self._config.deployment_resource(),
            },
            "inputs": [{"text": message}],
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        _log.debug("CESClient.run_turn: POST %s", url)
        with httpx.Client(timeout=60) as http:
            resp = http.post(url, json=body, headers=headers)

        if resp.status_code in (401, 403):
            raise CESAuthError(
                f"CES authentication failed (HTTP {resp.status_code}). "
                "Your gcloud token may have expired. Run: gcloud auth login"
            )

        resp.raise_for_status()
        data = resp.json()
        outputs = data.get("outputs", [])
        if outputs and isinstance(outputs, list):
            first = outputs[0]
            if isinstance(first, dict):
                return str(first.get("text", ""))
        return ""

    # ------------------------------------------------------------------

    def new_session_id(self, length: int = 16) -> str:
        """Return a random alphanumeric session ID.

        Args:
            length: Number of characters (default 16).
        """
        alphabet = string.ascii_letters + string.digits
        return "".join(random.choices(alphabet, k=length))
