"""App environment detector — discovers startup commands, .env files, and deployment URLs.

Scans a repository's file tree (as ``(path, content)`` tuples already loaded
by the extractor) to extract:

  - How to run the application locally (startup commands with source)
  - ``.env`` / dotenv files and the environment variable *keys* they contain
  - Local dev URL inferred from ``PORT``/``HOST`` env vars or startup command hints
  - Staging and production URLs found in env files and config files

No network calls are made.  Secret values are returned in ``env_vars`` for
use by the launcher but are never stored in the SBOM JSON.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENV_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.dev",
        ".env.development",
        ".env.staging",
        ".env.stage",
        ".env.production",
        ".env.prod",
        ".env.qa",
        ".env.test",
        ".env.example",
        ".env.sample",
        ".env.template",
        ".envrc",
    }
)

# Env var names that indicate the local server port
_PORT_VAR_NAMES: frozenset[str] = frozenset(
    {
        "PORT",
        "APP_PORT",
        "SERVER_PORT",
        "HTTP_PORT",
        "BACKEND_PORT",
        "API_PORT",
        "LISTEN_PORT",
    }
)

# Env var names that indicate a deployment URL (checked case-insensitively)
_URL_VAR_SUFFIXES = (
    "url",
    "host",
    "base",
    "endpoint",
    "server",
    "backend",
    "api",
    "origin",
)
_URL_VAR_EXACT: frozenset[str] = frozenset(
    {
        "APP_URL",
        "API_URL",
        "BASE_URL",
        "SERVER_URL",
        "BACKEND_URL",
        "NEXT_PUBLIC_API_URL",
        "VITE_API_URL",
        "VITE_APP_API_URL",
        "REACT_APP_API_URL",
        "NUXT_PUBLIC_API_URL",
        "STAGING_URL",
        "PROD_URL",
        "PRODUCTION_URL",
        "QA_URL",
        "DEPLOYMENT_URL",
        "PUBLIC_URL",
        "VERCEL_URL",
    }
)

_STAGING_INDICATORS = frozenset({"staging", "stage", "qa", "uat", "preprod", "pre-prod", "test"})
_PROD_INDICATORS = frozenset({"prod", "production", "live", "release"})

# Log path detection patterns
_LOG_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r'logging\.basicConfig\(.*?filename\s*=\s*[\'\"](.*?)[\'\"]', re.DOTALL),
    re.compile(r'FileHandler\([\'\"](.*?)[\'\"]'),
    re.compile(r'logger\.add\([\'\"](.*?\.log[^\'\"]*)[\'\"]\)'),
    re.compile(r'LOG_FILE\s*=\s*[\'\"](.*?)[\'\"]'),
    re.compile(r'LOG_PATH\s*=\s*[\'\"](.*?)[\'\"]'),
]

# Env var keys that may hold log file paths
_LOG_ENV_VAR_KEYS: frozenset[str] = frozenset(
    {"LOG_FILE", "LOG_PATH", "APP_LOG", "LOGGING_FILE", "LOG_FILENAME"}
)

_PORT_PATTERN = re.compile(r"^\d{2,5}$")
_COMPOSE_PORT_PATTERN = re.compile(r'["\'"]?(\d{2,5}):(\d{2,5})')

# Startup-command patterns in source files
_UVICORN_CALL = re.compile(r"uvicorn\.run\(|uvicorn\s+\S+:\S+", re.IGNORECASE)
_FLASK_RUN = re.compile(r"app\.run\(")
_FASTAPI_MAIN = re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_env_filename(basename: str) -> bool:
    return basename in _ENV_FILE_NAMES or bool(re.match(r"^\.env(\.[a-z0-9_-]+)?$", basename))


def _canonicalize_url(raw: str) -> str | None:
    """Return a clean https?:// URL or None if the value is not a real URL."""
    v = (raw or "").strip()
    if not v.startswith(("http://", "https://")):
        return None
    if any(tok in v for tok in ("${", "{{", "$", "<", ">")):
        return None
    try:
        parsed = urlparse(v)
        if not parsed.netloc:
            return None
    except Exception:
        return None
    return v.rstrip("/")


def _classify_url(url: str, var_name: str = "") -> str:
    """Return 'local', 'staging', or 'production' for a URL."""
    low_url = url.lower()
    low_name = var_name.lower()
    if "localhost" in low_url or "127.0.0.1" in low_url or "0.0.0.0" in low_url:
        return "local"
    combined = f"{low_url} {low_name}"
    if any(ind in combined for ind in _PROD_INDICATORS):
        return "production"
    if any(ind in combined for ind in _STAGING_INDICATORS):
        return "staging"
    # Unknown external URL — treat as staging so it's surfaced but not promoted to prod
    return "staging"


def _is_url_key(key: str) -> bool:
    """True when the env var name suggests it holds a deployment URL."""
    upper = key.upper()
    if upper in _URL_VAR_EXACT:
        return True
    low = key.lower()
    return any(low.endswith(suffix) or low.startswith(suffix) for suffix in _URL_VAR_SUFFIXES)


# ---------------------------------------------------------------------------
# .env file parser
# ---------------------------------------------------------------------------


def _parse_env_file(content: str) -> dict[str, str]:
    """Parse a dotenv file and return key→value pairs (quoted values handled).

    Comment lines (``#``) and lines without ``=`` are skipped.
    """
    result: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        # Key must be a valid identifier
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        value = raw_value.strip()
        # Strip inline comment (only when value is not quoted)
        if value and value[0] not in ('"', "'"):
            comment_pos = value.find(" #")
            if comment_pos != -1:
                value = value[:comment_pos].strip()
        value = value.strip('"\'')
        result[key] = value
    return result


# ---------------------------------------------------------------------------
# Startup command extractors
# ---------------------------------------------------------------------------


def _startup_from_package_json(path: str, content: str) -> list[dict[str, str]]:
    try:
        data = json.loads(content)
    except Exception:
        return []
    scripts = data.get("scripts") or {}
    priority = ["dev", "start", "serve", "local", "preview"]
    cmds: list[dict[str, str]] = []
    for name in priority:
        cmd = scripts.get(name)
        if cmd and isinstance(cmd, str):
            label = "dev" if name in ("dev", "local") else "start"
            cmds.append({"command": f"npm run {name}", "source": path, "label": label})
    return cmds


def _startup_from_makefile(path: str, content: str) -> list[dict[str, str]]:
    priority_targets = {"dev", "run", "start", "serve", "local", "up"}
    cmds: list[dict[str, str]] = []
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^([\w-]+)\s*:", line)
        if m and m.group(1).lower() in priority_targets:
            target = m.group(1)
            label = "dev" if target.lower() in ("dev", "local") else "start"
            cmds.append({"command": f"make {target}", "source": path, "label": label})
    return cmds


def _startup_from_procfile(path: str, content: str) -> list[dict[str, str]]:
    m = re.search(r"^web\s*:\s*(.+)", content, re.MULTILINE)
    if m:
        return [{"command": m.group(1).strip(), "source": path, "label": "start"}]
    return []


def _startup_from_pyproject(path: str, content: str) -> list[dict[str, str]]:
    cmds: list[dict[str, str]] = []
    for m in re.finditer(
        r'(dev|run|start|serve|local)\s*=\s*["\']([^"\']+)["\']', content
    ):
        name, cmd = m.group(1), m.group(2)
        label = "dev" if name in ("dev", "local") else "start"
        cmds.append({"command": cmd, "source": path, "label": label})
    return cmds


def _startup_from_compose(path: str, content: str) -> list[dict[str, str]]:
    """Suggest docker compose up as a startup command when compose file is present."""
    return [{"command": "docker compose up", "source": path, "label": "dev"}]


def _infer_python_entrypoint(files: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    """Detect Python entry points from main.py / app.py patterns."""
    candidates = [
        "main.py",
        "app.py",
        "server.py",
        "run.py",
        "backend/main.py",
        "src/main.py",
        "api/main.py",
    ]
    path_map = {p.lower(): (p, c) for p, c in files}
    for rel in candidates:
        hit = path_map.get(rel)
        if not hit:
            continue
        real_path, content = hit
        text = content or ""
        if _UVICORN_CALL.search(text) or _FLASK_RUN.search(text) or _FASTAPI_MAIN.search(text):
            return [{"command": f"python {real_path}", "source": real_path, "label": "start"}]
    return []


# ---------------------------------------------------------------------------
# Port / URL inference helpers
# ---------------------------------------------------------------------------


def _default_port_for_command(cmd: str) -> str | None:
    """Guess default dev port from the startup command."""
    low = cmd.lower()
    if "uvicorn" in low or "fastapi" in low or "gunicorn" in low:
        return "8000"
    if "flask" in low:
        return "5000"
    if "django" in low:
        return "8000"
    if "npm" in low or "node" in low or "next" in low or "vite" in low:
        return "3000"
    if "rails" in low or "ruby" in low:
        return "3000"
    return None


# ---------------------------------------------------------------------------
# Log path detection
# ---------------------------------------------------------------------------


def _detect_log_paths(files: Sequence[tuple[str, str]], env_vars: dict[str, str]) -> list[str]:
    """Detect log file paths from source files and env vars.

    Searches file contents for Python logging config patterns, checks env vars
    for log path values, and scans for common log directory prefixes.

    Parameters
    ----------
    files:
        ``(relative_path, file_content)`` tuples to scan.
    env_vars:
        Parsed env vars dict (from dotenv files) to check for log path keys.

    Returns
    -------
    Deduplicated list of log file path strings.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        """Add a path if it looks like a file and is not a duplicate."""
        p = path.strip()
        if not p:
            return
        # Skip paths that are clearly non-file (no extension and no directory separator)
        if "/" not in p and "." not in p:
            return
        if p not in seen:
            seen.add(p)
            found.append(p)

    # 1. Scan source file contents for logging config patterns
    for _path, content in files:
        text = content or ""
        for pattern in _LOG_PATH_PATTERNS:
            for m in pattern.findall(text):
                _add(m)

    # 2. Check env var values for log path keys
    for key, value in env_vars.items():
        if key.upper() in _LOG_ENV_VAR_KEYS:
            if value and ("/" in value or ".log" in value):
                _add(value)

    # 3. Detect common log directories from fixture file paths
    log_dir_prefixes: set[str] = set()
    for path, _ in files:
        lower = path.lower()
        if lower.startswith("logs/") or lower.startswith("log/"):
            prefix = path.split("/")[0]
            log_dir_prefixes.add(prefix)
    for prefix in sorted(log_dir_prefixes):
        _add(f"{prefix}/*.log")

    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_app_env(files: Sequence[tuple[str, str]]) -> dict:
    """Scan repository files and return app environment discovery results.

    Parameters
    ----------
    files:
        ``(relative_path, file_content)`` tuples — the same slice passed to
        ``build_scan_summary``.

    Returns
    -------
    dict with keys:

    ``startup_commands``
        list of ``{command, source, label}`` dicts ordered by preference.
        ``label`` is ``"dev"`` (hot-reload) or ``"start"`` (production-style).

    ``env_files``
        Relative paths to discovered dotenv files.

    ``env_var_keys``
        Sorted list of env var *keys* found across all dotenv files
        (never includes values — safe for serialization).

    ``env_vars``
        Full ``key → value`` map merged from all dotenv files.
        **Not serialized into the SBOM** — used only by the launcher to
        populate the subprocess environment.

    ``local_url``
        Inferred local dev URL (e.g. ``http://localhost:8000``) or ``None``.

    ``staging_urls``
        Staging / QA URLs discovered from env files or config files.

    ``production_urls``
        Production URLs discovered from env files or config files.
    """
    startup_commands: list[dict[str, str]] = []
    env_files: list[str] = []
    # Merged env vars from all .env files (most-specific file wins)
    env_vars: dict[str, str] = {}
    staging_urls: list[str] = []
    production_urls: list[str] = []
    local_port: str | None = None

    # ── Pass 1: classify each file and collect data ────────────────────────
    for path, content in files:
        basename = path.split("/")[-1].lower()
        text = content or ""

        # .env files
        if _is_env_filename(basename):
            env_files.append(path)
            parsed = _parse_env_file(text)
            env_vars.update(parsed)

            for key, value in parsed.items():
                # PORT detection
                if key.upper() in _PORT_VAR_NAMES and _PORT_PATTERN.match(value):
                    local_port = value

                # URL detection from env vars
                if _is_url_key(key):
                    url = _canonicalize_url(value)
                    if url:
                        kind = _classify_url(url, key)
                        if kind == "staging":
                            staging_urls.append(url)
                        elif kind == "production":
                            production_urls.append(url)
            continue

        # package.json (skip node_modules)
        if "node_modules" in path:
            continue
        if basename == "package.json":
            startup_commands.extend(_startup_from_package_json(path, text))
        elif basename in ("makefile", "gnumakefile") or path.endswith("/Makefile") or path == "Makefile":
            startup_commands.extend(_startup_from_makefile(path, text))
        elif basename == "procfile":
            startup_commands.extend(_startup_from_procfile(path, text))
        elif basename == "pyproject.toml":
            startup_commands.extend(_startup_from_pyproject(path, text))
        elif re.search(r"docker.?compose", basename):
            startup_commands.extend(_startup_from_compose(path, text))
            # Also grab port mappings from compose for local URL
            if local_port is None:
                m = _COMPOSE_PORT_PATTERN.search(text)
                if m:
                    local_port = m.group(2)  # container-side port

    # ── Pass 2: infer Python entry point if no commands found ──────────────
    if not startup_commands:
        inferred = _infer_python_entrypoint(files)
        startup_commands.extend(inferred)
        # Also infer port from the entry point file content
        # e.g. uvicorn.run(app, host="0.0.0.0", port=8000)
        if inferred and local_port is None:
            src = inferred[0]["source"]
            for fpath, fcontent in files:
                if fpath == src:
                    m = re.search(r"uvicorn\.run\([^)]*port\s*=\s*(\d+)", fcontent or "")
                    if m:
                        local_port = m.group(1)
                    break

    # ── Pass 3: deduplicate startup commands ───────────────────────────────
    seen_cmds: set[str] = set()
    unique_cmds: list[dict[str, str]] = []
    for cmd in startup_commands:
        key = cmd["command"]
        if key not in seen_cmds:
            seen_cmds.add(key)
            unique_cmds.append(cmd)
    startup_commands = unique_cmds

    # ── Pass 4: infer local URL ────────────────────────────────────────────
    local_url: str | None = None
    port = local_port or (
        _default_port_for_command(startup_commands[0]["command"])
        if startup_commands
        else None
    )
    if port:
        local_url = f"http://localhost:{port}"

    # ── Pass 5: deduplicate URL lists ──────────────────────────────────────
    def _dedup(lst: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in lst:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    # ── Pass 6: detect log paths ───────────────────────────────────────────
    log_paths = _detect_log_paths(files, env_vars)

    return {
        "startup_commands": startup_commands,
        "env_files": env_files,
        "env_var_keys": sorted(env_vars.keys()),
        "env_vars": env_vars,
        "local_url": local_url,
        "staging_urls": _dedup(staging_urls),
        "production_urls": _dedup(production_urls),
        "log_paths": log_paths,
    }
