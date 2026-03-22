"""AppLauncher — starts a local AI application and waits until it is ready.

Used by the redteam CLI when no ``--target`` URL is provided but the SBOM
contains a startup command.  The launcher:

1. Spawns the app as a subprocess using the discovered startup command.
2. Loads env vars from all discovered ``.env`` files (merged, most-specific wins).
3. Polls the chat / health endpoint until it responds (up to ``startup_timeout_s``).
4. Yields the local URL so the caller can run the redteam scan.
5. On exit (or exception), terminates the subprocess and its children.

Usage::

    from nuguard.redteam.launcher.app_launcher import AppLauncher

    async with AppLauncher.from_sbom(sbom, source_dir) as launcher:
        print(launcher.url)   # http://localhost:8000
        # run scan …
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import signal
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

_log = logging.getLogger(__name__)

_DEFAULT_STARTUP_TIMEOUT = 60.0  # seconds to wait for the app to answer
_DEFAULT_POLL_INTERVAL = 1.0  # seconds between health-check polls
_PROBE_PATHS = ["/health", "/healthz", "/ready", "/ping", "/", "/chat"]


class AppLaunchError(Exception):
    """Raised when the app process fails to start or become healthy."""


class AppLauncher:
    """Async context manager that starts a local app and returns its URL.

    Parameters
    ----------
    command:
        Shell command string to start the app (e.g. ``"npm run dev"``).
    working_dir:
        Directory to run the command in (defaults to ``cwd``).
    env_vars:
        Extra environment variables injected into the subprocess.
        These come from discovered ``.env`` files and are merged on top of
        the current process environment (``os.environ``).
    local_url:
        Expected local URL (e.g. ``"http://localhost:8000"``).
    chat_path:
        Chat endpoint path used for health probing (e.g. ``"/chat"``).
    startup_timeout_s:
        Seconds to wait for the app to become healthy before giving up.
    """

    def __init__(
        self,
        command: str,
        working_dir: Path | None = None,
        env_vars: dict[str, str] | None = None,
        local_url: str = "http://localhost:8000",
        chat_path: str = "/chat",
        startup_timeout_s: float = _DEFAULT_STARTUP_TIMEOUT,
    ) -> None:
        self._command = command
        self._working_dir = working_dir or Path.cwd()
        self._env_vars = env_vars or {}
        self._local_url = local_url.rstrip("/")
        self._chat_path = chat_path
        self._startup_timeout = startup_timeout_s
        self._process: asyncio.subprocess.Process | None = None
        self._drain_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_sbom(
        cls,
        sbom: "AiSbomDocument",
        source_dir: Path,
        env_override: dict[str, str] | None = None,
        startup_timeout_s: float = _DEFAULT_STARTUP_TIMEOUT,
    ) -> "AppLauncher":
        """Build an AppLauncher from a pre-scanned SBOM and the source directory.

        Reads ``.env`` files from disk (relative to ``source_dir``) to get the
        actual env var *values* — values are never stored in the SBOM JSON.
        Merges: os.environ → .env files → ``env_override`` (highest priority).
        """
        summary = sbom.summary
        if not summary:
            raise AppLaunchError("SBOM has no summary — re-scan with app_env_detector enabled.")
        if not summary.startup_commands:
            raise AppLaunchError(
                "No startup commands found in SBOM.  "
                "Run 'nuguard sbom generate' from the app source directory."
            )

        # Pick first startup command (prefer 'dev' label)
        cmds = sorted(
            summary.startup_commands,
            key=lambda c: 0 if c.get("label") == "dev" else 1,
        )
        chosen = cmds[0]["command"]

        # Load env vars from disk
        env_vars = _load_env_files(source_dir, summary.env_files)
        if env_override:
            env_vars.update(env_override)

        local_url = summary.local_url or "http://localhost:8000"

        # Determine chat path from SBOM API_ENDPOINT nodes
        from nuguard.sbom.models import NodeType

        chat_path = "/chat"
        for node in sbom.nodes:
            if node.component_type == NodeType.API_ENDPOINT:
                meta = node.metadata
                if meta and meta.chat_payload_key and meta.endpoint:
                    chat_path = meta.endpoint
                    break

        _log.info(
            "[launcher] Startup command: %r  url=%s  env_files=%d",
            chosen,
            local_url,
            len(summary.env_files),
        )
        return cls(
            command=chosen,
            working_dir=source_dir,
            env_vars=env_vars,
            local_url=local_url,
            chat_path=chat_path,
            startup_timeout_s=startup_timeout_s,
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def url(self) -> str:
        """The local URL where the app is (or will be) reachable."""
        return self._local_url

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AppLauncher":
        await self._start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._stop()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _start(self) -> None:
        """Spawn the subprocess and wait for the app to become healthy."""
        merged_env = {**os.environ, **self._env_vars}

        _log.info("[launcher] Starting: %s  (cwd=%s)", self._command, self._working_dir)
        args = shlex.split(self._command)
        try:
            self._process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(self._working_dir),
                env=merged_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise AppLaunchError(
                f"Could not start app — command not found: {self._command!r}.  "
                "Make sure all dependencies are installed."
            ) from exc

        _log.info("[launcher] PID %d — waiting up to %.0fs for health check …", self._process.pid, self._startup_timeout)
        try:
            await asyncio.wait_for(self._wait_healthy(), timeout=self._startup_timeout)
        except asyncio.TimeoutError as exc:
            await self._stop()
            raise AppLaunchError(
                f"App did not become healthy within {self._startup_timeout:.0f}s.  "
                f"Command: {self._command!r}  URL: {self._local_url}"
            ) from exc

        _log.info("[launcher] App is healthy at %s", self._local_url)
        # Start a background task that continuously drains stdout/stderr so the
        # pipe never fills and blocks the app process during a long scan.
        self._drain_task = asyncio.ensure_future(self._drain_output())

    async def _drain_output(self) -> None:
        """Read and log all stdout/stderr output from the app process."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode(errors="replace").rstrip()
                if decoded:
                    _log.debug("[app] %s", decoded)
        except Exception:
            pass

    async def _wait_healthy(self) -> None:
        """Poll probe paths until one returns a non-5xx response."""
        probe_urls = [f"{self._local_url}{p}" for p in [self._chat_path] + _PROBE_PATHS]
        probe_urls = list(dict.fromkeys(probe_urls))  # deduplicate preserving order

        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0), follow_redirects=True) as client:
            while True:
                for probe_url in probe_urls:
                    try:
                        resp = await client.get(probe_url)
                        if resp.status_code < 500:
                            return  # App is accepting requests
                    except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                        pass  # Not ready yet
                    except Exception:
                        pass

                # Check whether the process has already died
                if self._process and self._process.returncode is not None:
                    stdout = ""
                    if self._process.stdout:
                        try:
                            raw = await asyncio.wait_for(
                                self._process.stdout.read(4096), timeout=1.0
                            )
                            stdout = raw.decode(errors="replace")
                        except Exception:
                            pass
                    raise AppLaunchError(
                        f"App process exited with code {self._process.returncode} before becoming healthy.  "
                        f"Output: {stdout[:500]!r}"
                    )

                await asyncio.sleep(_DEFAULT_POLL_INTERVAL)

    async def _stop(self) -> None:
        """Terminate the subprocess and all its children."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return
        _log.info("[launcher] Stopping app (PID %d) …", proc.pid)
        try:
            # Send SIGTERM to the process group so child processes also stop
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
        self._process = None
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
        _log.info("[launcher] App stopped.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env_files(source_dir: Path, env_file_paths: list[str]) -> dict[str, str]:
    """Read .env files from disk and return merged key→value dict.

    Later files in the list override earlier ones (more-specific wins).
    Files that don't exist are silently skipped.
    """
    from nuguard.sbom.core.app_env_detector import _parse_env_file

    merged: dict[str, str] = {}
    # Sort so .env comes before .env.local (more specific files last = highest priority)
    sorted_paths = sorted(env_file_paths, key=lambda p: len(p))
    for rel_path in sorted_paths:
        full = source_dir / rel_path
        if not full.exists():
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
            parsed = _parse_env_file(text)
            merged.update(parsed)
            _log.debug("[launcher] Loaded %d vars from %s", len(parsed), rel_path)
        except Exception as exc:
            _log.debug("[launcher] Could not read %s: %s", rel_path, exc)
    return merged


def pick_target_url(sbom: "AiSbomDocument", prefer: str = "local") -> str | None:
    """Choose the best target URL from SBOM summary without launching anything.

    Preference order when ``prefer='local'``:  local_url → staging_urls[0] → production_urls[0] → deployment_urls[0].
    Preference order when ``prefer='staging'``: staging_urls[0] → production_urls[0] → local_url → deployment_urls[0].
    Preference order when ``prefer='production'``: production_urls[0] → staging_urls[0] → local_url → deployment_urls[0].

    Returns ``None`` when no URL can be determined.
    """
    summary = sbom.summary
    if not summary:
        return None

    if prefer == "local":
        candidates = [
            summary.local_url,
            *(summary.staging_urls or []),
            *(summary.production_urls or []),
            *(summary.deployment_urls or []),
        ]
    elif prefer == "staging":
        candidates = [
            *(summary.staging_urls or []),
            *(summary.production_urls or []),
            summary.local_url,
            *(summary.deployment_urls or []),
        ]
    else:  # production
        candidates = [
            *(summary.production_urls or []),
            *(summary.staging_urls or []),
            summary.local_url,
            *(summary.deployment_urls or []),
        ]

    for url in candidates:
        if url and isinstance(url, str):
            return url.rstrip("/")
    return None
