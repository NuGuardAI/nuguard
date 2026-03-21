"""Manages fixture app lifecycle for E2E redteam tests.

Each fixture app is:
  1. Materialized from its cached_files.json into a temp directory
  2. Given a dedicated virtualenv with its own requirements installed
  3. Started as a subprocess on a fixed local port
  4. Health-checked before the test begins
  5. Torn down and cleaned up after the test

Virtualenvs are cached under VENV_CACHE_DIR between test runs to avoid the cost
of a full ``pip install`` on every invocation.  Delete that directory to force
a clean rebuild.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

_log = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "benchmark" / "fixtures"
VENV_CACHE_DIR = Path(tempfile.gettempdir()) / "nuguard_redteam_venvs"

# Timeouts (seconds)
PIP_INSTALL_TIMEOUT = 300
APP_STARTUP_TIMEOUT = 60
HEALTH_POLL_INTERVAL = 1.0


@dataclass
class AppConfig:
    """Everything the runner needs to extract, install, start, and probe a fixture app."""

    name: str
    fixture_dir: str
    requirements_path: str           # relative to fixture root
    startup_cwd: str                  # relative to fixture root; "" = fixture root
    startup_module: str               # e.g. "api:app" or "app.main:app" for uvicorn
    startup_framework: str = "uvicorn"  # "uvicorn" | "flask"
    port: int = 18100
    health_path: str = "/health"
    chat_path: str = "/chat"
    required_env_vars: list[str] = field(default_factory=list)
    optional_env_vars: dict[str, str] = field(default_factory=dict)  # name -> description
    policy_file: str | None = None    # relative to fixture root
    notes: str = ""
    # Chat request schema
    chat_payload_key: str = "message"          # body key for the user's prompt
    chat_payload_list: bool = False             # wrap payload in a list (e.g. {"phrases": [...]})


# ---------------------------------------------------------------------------
# Canonical fixture configurations
# ---------------------------------------------------------------------------

APP_CONFIGS: dict[str, AppConfig] = {
    "openai-cs-agents-demo": AppConfig(
        name="openai-cs-agents-demo",
        fixture_dir="openai-cs-agents-demo",
        requirements_path="python-backend/requirements.txt",
        startup_cwd="python-backend",
        startup_module="api:app",
        port=18100,
        health_path="/health",
        chat_path="/chat",
        required_env_vars=["OPENAI_API_KEY"],
        policy_file="cognitive_policy.md",
        notes="Multi-agent airline customer service. Requires OpenAI API key.",
        chat_payload_key="message",       # POST /chat {"message": "..."}
    ),
    "rag-chatbot-demo": AppConfig(
        name="rag-chatbot-demo",
        fixture_dir="rag-chatbot-demo",
        requirements_path="backend/requirements.txt",
        startup_cwd="backend",
        startup_module="app.main:app",
        port=18101,
        health_path="/docs",   # FastAPI auto-generates /docs; no explicit /health
        chat_path="/chat",
        required_env_vars=["OPENAI_API_KEY"],
        notes=(
            "RAG chatbot with LangChain + FAISS. "
            "Requires FAISS indexes — app will start but /chat returns 500 if indexes "
            "are absent. Generate with: backend/scripts/generate_indexes.py"
        ),
        chat_payload_key="user_query",    # POST /chat {"user_query": "..."}
    ),
    "real-estate-agent": AppConfig(
        name="real-estate-agent",
        fixture_dir="real-estate-agent",
        requirements_path="requirements.txt",
        startup_cwd="",
        startup_module="api:app",
        port=18102,
        health_path="/health",
        chat_path="/analyze",
        required_env_vars=["OPENAI_API_KEY", "FIRECRAWL_API_KEY"],
        notes="Real-estate market analysis agent. Requires OpenAI and Firecrawl API keys.",
        chat_payload_key="query",         # POST /analyze {"query": "..."} (best guess)
    ),
    "voicelive-api-salescoach-demo": AppConfig(
        name="voicelive-api-salescoach-demo",
        fixture_dir="voicelive-api-salescoach-demo",
        requirements_path="backend/requirements.txt",
        startup_cwd="backend",
        startup_module="src.app",
        startup_framework="flask",
        port=18103,
        health_path="/api/config",
        chat_path="/api/analyze",
        required_env_vars=[],
        optional_env_vars={
            "AZURE_SPEECH_KEY": "Azure speech service (voice features disabled without it)",
        },
        notes=(
            "Voice sales coach agent using Flask + Azure AI. "
            "Starts without Azure credentials; voice/analysis features will error."
        ),
        chat_payload_key="transcript",    # POST /api/analyze {"transcript": "...", "scenario_id": "..."}
    ),
    "healthcare-voice-agent": AppConfig(
        name="healthcare-voice-agent",
        fixture_dir="Healthcare-voice-agent",
        requirements_path="backend/requirements.txt",
        startup_cwd="backend",
        startup_module="main:app",
        port=18104,
        health_path="/api/health",
        chat_path="/run_langgraph",
        required_env_vars=["OPENAI_API_KEY"],
        optional_env_vars={
            "DATABASE_URL": "PostgreSQL for patient data (app starts without it; DB calls fail)",
        },
        policy_file="cognitive_policy.md",
        notes=(
            "Healthcare LangGraph voice agent. "
            "DB-backed endpoints (/patient-details, /triage) need DATABASE_URL."
        ),
        chat_payload_key="phrases",       # POST /run_langgraph {"phrases": ["..."]}
        chat_payload_list=True,
    ),
}


# ---------------------------------------------------------------------------
# AppRunner
# ---------------------------------------------------------------------------

class AppRunner:
    """Manages the full lifecycle of a single fixture app subprocess."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._source_dir: Path | None = None
        self._venv_dir: Path = VENV_CACHE_DIR / config.name
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]
        self.started: bool = False
        self.start_error: str | None = None
        self.base_url: str = f"http://127.0.0.1:{config.port}"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def materialize(self) -> None:
        """Extract source files into a temp directory (does not start the app)."""
        self._source_dir = Path(self._materialize_fixture())

    def setup(self) -> None:
        """Extract source files, create venv, install deps, and start the app."""
        try:
            if self._source_dir is None:
                self._source_dir = Path(self._materialize_fixture())
            self._ensure_venv()
            self._start_process()
            self._wait_for_health()
            self.started = True
            _log.info("[%s] app is ready at %s", self.config.name, self.base_url)
        except Exception as exc:
            self.start_error = str(exc)
            _log.warning("[%s] failed to start: %s", self.config.name, exc)
            self._kill_process()

    def teardown(self) -> None:
        """Stop the app subprocess and clean up the temp source directory."""
        self._kill_process()
        if self._source_dir and self._source_dir.exists():
            shutil.rmtree(self._source_dir, ignore_errors=True)
            self._source_dir = None

    @property
    def source_dir(self) -> Path:
        """Path to the materialized source directory (set after setup)."""
        if self._source_dir is None:
            raise RuntimeError("App not yet set up — call setup() first")
        return self._source_dir

    def working_dir(self) -> Path:
        """Absolute path to the app's working directory inside source_dir."""
        if self.config.startup_cwd:
            return self.source_dir / self.config.startup_cwd
        return self.source_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _materialize_fixture(self) -> str:
        """Extract cached_files.json to a fresh temp directory."""
        cache_path = FIXTURES_DIR / self.config.fixture_dir / "cached_files.json"
        if not cache_path.exists():
            raise FileNotFoundError(f"Fixture cache not found: {cache_path}")
        payload: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8"))
        files: list[dict] = payload.get("files", [])
        temp_dir = tempfile.mkdtemp(prefix=f"nuguard-e2e-{self.config.name}-")
        root = Path(temp_dir)
        for entry in files:
            rel_path = str(entry.get("path", "")).strip()
            if not rel_path:
                continue
            target = root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(entry.get("content", "")), encoding="utf-8")
        _log.debug("[%s] materialized %d files to %s", self.config.name, len(files), temp_dir)
        return temp_dir

    def _ensure_venv(self) -> None:
        """Create a virtualenv and install requirements; skip if sentinel file present."""
        VENV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sentinel = self._venv_dir / ".requirements_installed"
        req_file = self.source_dir / self.config.requirements_path

        if not req_file.exists():
            raise FileNotFoundError(f"Requirements not found: {req_file}")

        if not sentinel.exists():
            # Clean up any partial venv before recreating
            if self._venv_dir.exists():
                shutil.rmtree(self._venv_dir, ignore_errors=True)

            _log.info("[%s] creating virtualenv at %s", self.config.name, self._venv_dir)
            subprocess.run(
                [sys.executable, "-m", "venv", str(self._venv_dir)],
                check=True, timeout=60,
                capture_output=True,
            )
            _log.info("[%s] installing requirements (may take several minutes)…", self.config.name)
            subprocess.run(
                [str(self._venv_dir / "bin" / "pip"), "install", "--quiet",
                 "-r", str(req_file)],
                check=True, timeout=PIP_INSTALL_TIMEOUT,
                capture_output=True,
            )
            # Ensure the ASGI/WSGI server is always present regardless of requirements.txt
            framework = self.config.startup_framework
            server_pkg = "flask" if framework == "flask" else "uvicorn[standard]"
            subprocess.run(
                [str(self._venv_dir / "bin" / "pip"), "install", "--quiet", server_pkg],
                check=True, timeout=60,
                capture_output=True,
            )
            _log.debug("[%s] installed server package: %s", self.config.name, server_pkg)
            sentinel.touch()
        else:
            _log.debug("[%s] venv cache hit — skipping install", self.config.name)

    def _build_startup_env(self) -> dict[str, str]:
        """Build the environment for the subprocess (inherits + fixture-specific vars)."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.working_dir())
        env["PORT"] = str(self.config.port)
        # Silence noisy startup logs from the target app
        env.setdefault("LOG_LEVEL", "warning")
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    def _start_process(self) -> None:
        """Launch the app subprocess."""
        python_bin = str(self._venv_dir / "bin" / "python")
        cwd = str(self.working_dir())

        if self.config.startup_framework == "flask":
            cmd = [
                python_bin, "-m", "flask",
                "--app", self.config.startup_module,
                "run",
                "--host", "127.0.0.1",
                "--port", str(self.config.port),
                "--no-debugger", "--no-reload",
            ]
        else:  # uvicorn
            cmd = [
                python_bin, "-m", "uvicorn",
                self.config.startup_module,
                "--host", "127.0.0.1",
                "--port", str(self.config.port),
                "--no-access-log",
            ]

        _log.info("[%s] starting: %s (cwd=%s)", self.config.name, " ".join(cmd), cwd)
        self._process = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=self._build_startup_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def _wait_for_health(self) -> None:
        """Poll the health endpoint until the app is ready or the timeout expires."""
        url = f"{self.base_url}{self.config.health_path}"
        deadline = time.monotonic() + APP_STARTUP_TIMEOUT
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            # Check if process died already
            if self._process and self._process.poll() is not None:
                stderr = self._process.stderr.read().decode(errors="replace") if self._process.stderr else ""
                raise RuntimeError(
                    f"App process exited (rc={self._process.returncode}) before health check passed.\n"
                    f"stderr:\n{stderr[-2000:]}"
                )
            try:
                resp = httpx.get(url, timeout=3.0)
                if resp.status_code < 500:
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(HEALTH_POLL_INTERVAL)

        raise TimeoutError(
            f"App did not become healthy at {url} within {APP_STARTUP_TIMEOUT}s. "
            f"Last error: {last_error}"
        )

    def _kill_process(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
