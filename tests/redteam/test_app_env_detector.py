"""Unit tests for app_env_detector and AppLauncher helpers."""
from __future__ import annotations

from nuguard.sbom.core.app_env_detector import (
    _canonicalize_url,
    _classify_url,
    _parse_env_file,
    detect_app_env,
)

# ── _parse_env_file ──────────────────────────────────────────────────────────


def test_parse_env_file_basic() -> None:
    content = """
PORT=8000
DATABASE_URL=postgresql://localhost/mydb
API_KEY=secret-value
# comment line
EMPTY=
"""
    result = _parse_env_file(content)
    assert result["PORT"] == "8000"
    assert result["DATABASE_URL"] == "postgresql://localhost/mydb"
    assert result["API_KEY"] == "secret-value"
    assert result["EMPTY"] == ""


def test_parse_env_file_quoted_values() -> None:
    content = 'APP_NAME="My App"\nSECRET=\'hello world\'\n'
    result = _parse_env_file(content)
    assert result["APP_NAME"] == "My App"
    assert result["SECRET"] == "hello world"


def test_parse_env_file_inline_comment() -> None:
    content = "PORT=3000 # default dev port\n"
    result = _parse_env_file(content)
    assert result["PORT"] == "3000"


def test_parse_env_file_skips_invalid_lines() -> None:
    content = "NOT-VALID\n=NOKEY\n123BAD=value\nGOOD_KEY=ok\n"
    result = _parse_env_file(content)
    assert "GOOD_KEY" in result
    assert len(result) == 1


# ── _canonicalize_url ────────────────────────────────────────────────────────


def test_canonicalize_url_valid() -> None:
    assert _canonicalize_url("https://api.example.com/") == "https://api.example.com"
    assert _canonicalize_url("http://localhost:3000") == "http://localhost:3000"


def test_canonicalize_url_rejects_templates() -> None:
    assert _canonicalize_url("https://${HOST}/api") is None
    assert _canonicalize_url("https://{{domain}}/") is None


def test_canonicalize_url_rejects_non_http() -> None:
    assert _canonicalize_url("postgresql://localhost/db") is None
    assert _canonicalize_url("redis://localhost:6379") is None


# ── _classify_url ────────────────────────────────────────────────────────────


def test_classify_url_local() -> None:
    assert _classify_url("http://localhost:8000") == "local"
    assert _classify_url("http://127.0.0.1:5000") == "local"


def test_classify_url_staging() -> None:
    assert _classify_url("https://staging.myapp.com", "STAGING_URL") == "staging"
    assert _classify_url("https://qa.myapp.com", "QA_URL") == "staging"


def test_classify_url_production() -> None:
    assert _classify_url("https://myapp.com", "PRODUCTION_URL") == "production"
    assert _classify_url("https://api.myapp.com", "PROD_URL") == "production"


# ── detect_app_env ───────────────────────────────────────────────────────────


def test_detect_env_file_and_port() -> None:
    files = [
        (".env", "PORT=9000\nSECRET_KEY=abc123\n"),
    ]
    result = detect_app_env(files)
    assert result["local_url"] == "http://localhost:9000"
    assert ".env" in result["env_files"]
    assert "PORT" in result["env_var_keys"]
    assert "SECRET_KEY" in result["env_var_keys"]
    # env_vars contains actual values (for launcher use)
    assert result["env_vars"]["PORT"] == "9000"


def test_detect_package_json_startup() -> None:
    import json
    pkg = json.dumps({"scripts": {"dev": "next dev", "start": "next start"}})
    files = [("package.json", pkg)]
    result = detect_app_env(files)
    cmds = [c["command"] for c in result["startup_commands"]]
    assert "npm run dev" in cmds
    assert result["local_url"] == "http://localhost:3000"


def test_detect_makefile_startup() -> None:
    makefile = "dev:\n\tuvicorn main:app --reload\n\ntest:\n\tpytest\n"
    files = [("Makefile", makefile)]
    result = detect_app_env(files)
    cmds = [c["command"] for c in result["startup_commands"]]
    assert "make dev" in cmds


def test_detect_procfile_startup() -> None:
    files = [("Procfile", "web: gunicorn app:app --bind 0.0.0.0:$PORT\n")]
    result = detect_app_env(files)
    cmds = [c["command"] for c in result["startup_commands"]]
    assert any("gunicorn" in c for c in cmds)


def test_detect_python_entrypoint() -> None:
    main_py = 'if __name__ == "__main__":\n    uvicorn.run(app, host="0.0.0.0", port=8000)\n'
    files = [("main.py", main_py)]
    result = detect_app_env(files)
    cmds = [c["command"] for c in result["startup_commands"]]
    assert "python main.py" in cmds
    assert result["local_url"] == "http://localhost:8000"


def test_detect_staging_url_from_env() -> None:
    files = [
        (".env.staging", "API_URL=https://staging.api.example.com\n"),
    ]
    result = detect_app_env(files)
    assert "https://staging.api.example.com" in result["staging_urls"]


def test_detect_production_url_from_env() -> None:
    files = [
        (".env.production", "API_URL=https://api.example.com\nPRODUCTION_URL=https://api.example.com\n"),
    ]
    result = detect_app_env(files)
    assert "https://api.example.com" in result["production_urls"]


def test_env_var_keys_excludes_values_from_sbom() -> None:
    """env_var_keys should be safe for serialization — no secret values."""
    files = [(".env", "SECRET_KEY=super-secret\nPORT=8000\n")]
    result = detect_app_env(files)
    # Keys should be in env_var_keys
    assert "SECRET_KEY" in result["env_var_keys"]
    # Values should NOT be in env_var_keys (it's just keys)
    assert "super-secret" not in result["env_var_keys"]
    # But full values ARE in env_vars (for subprocess use)
    assert result["env_vars"]["SECRET_KEY"] == "super-secret"


def test_no_files_returns_empty() -> None:
    result = detect_app_env([])
    assert result["startup_commands"] == []
    assert result["env_files"] == []
    assert result["local_url"] is None


def test_docker_compose_startup_and_port() -> None:
    compose = """
version: '3'
services:
  api:
    build: .
    ports:
      - "8080:8000"
    command: uvicorn main:app
"""
    files = [("docker-compose.yml", compose)]
    result = detect_app_env(files)
    cmds = [c["command"] for c in result["startup_commands"]]
    assert "docker compose up" in cmds
    # Container port (8000) should be detected
    assert result["local_url"] == "http://localhost:8000"


# ── pick_target_url ──────────────────────────────────────────────────────────


def test_pick_target_url_prefers_local() -> None:
    from unittest.mock import MagicMock

    from nuguard.redteam.launcher.app_launcher import pick_target_url

    sbom = MagicMock()
    sbom.summary.local_url = "http://localhost:8000"
    sbom.summary.staging_urls = ["https://staging.example.com"]
    sbom.summary.production_urls = ["https://example.com"]
    sbom.summary.deployment_urls = []

    assert pick_target_url(sbom, prefer="local") == "http://localhost:8000"


def test_pick_target_url_falls_back_to_staging() -> None:
    from unittest.mock import MagicMock

    from nuguard.redteam.launcher.app_launcher import pick_target_url

    sbom = MagicMock()
    sbom.summary.local_url = None
    sbom.summary.staging_urls = ["https://staging.example.com"]
    sbom.summary.production_urls = []
    sbom.summary.deployment_urls = []

    assert pick_target_url(sbom, prefer="local") == "https://staging.example.com"


def test_pick_target_url_none_when_no_urls() -> None:
    from unittest.mock import MagicMock

    from nuguard.redteam.launcher.app_launcher import pick_target_url

    sbom = MagicMock()
    sbom.summary.local_url = None
    sbom.summary.staging_urls = []
    sbom.summary.production_urls = []
    sbom.summary.deployment_urls = []

    assert pick_target_url(sbom) is None
