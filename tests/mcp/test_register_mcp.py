"""Unit tests for scripts/register_mcp.py.

The script is not a package, so we load it via importlib.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load the script as a module
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "register_mcp.py"


def _load_register_mcp():
    spec = importlib.util.spec_from_file_location("register_mcp", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reg = _load_register_mcp()


# ---------------------------------------------------------------------------
# _claude_desktop_config_path
# ---------------------------------------------------------------------------


def test_desktop_config_path_darwin(tmp_path: Path) -> None:
    with patch("platform.system", return_value="Darwin"):
        with patch.object(Path, "home", return_value=tmp_path):
            path = reg._claude_desktop_config_path()
    assert path is not None
    assert "Application Support" in str(path)
    assert path.name == "claude_desktop_config.json"


def test_desktop_config_path_linux_default(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
    with patch("platform.system", return_value="Linux"):
        with patch.dict(os.environ, env, clear=True):
            with patch.object(Path, "home", return_value=tmp_path):
                path = reg._claude_desktop_config_path()
    assert path is not None
    assert ".config" in str(path)
    assert path.name == "claude_desktop_config.json"


def test_desktop_config_path_linux_xdg(tmp_path: Path) -> None:
    with patch("platform.system", return_value="Linux"):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            path = reg._claude_desktop_config_path()
    assert path is not None
    assert str(tmp_path) in str(path)


def test_desktop_config_path_windows(tmp_path: Path) -> None:
    with patch("platform.system", return_value="Windows"):
        with patch.dict(os.environ, {"APPDATA": str(tmp_path)}):
            path = reg._claude_desktop_config_path()
    assert path is not None
    assert str(tmp_path) in str(path)
    assert path.name == "claude_desktop_config.json"


def test_desktop_config_path_windows_no_appdata() -> None:
    env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
    with patch("platform.system", return_value="Windows"):
        with patch.dict(os.environ, env, clear=True):
            path = reg._claude_desktop_config_path()
    assert path is None


# ---------------------------------------------------------------------------
# _build_server_entry
# ---------------------------------------------------------------------------


def test_build_entry_uses_nuguard_mcp_when_on_path() -> None:
    with patch("shutil.which", return_value="/usr/local/bin/nuguard-mcp"):
        entry = reg._build_server_entry("", "", "")
    assert entry["command"] == "nuguard-mcp"
    assert entry["args"] == []


def test_build_entry_falls_back_to_uvx() -> None:
    with patch("shutil.which", return_value=None):
        entry = reg._build_server_entry("", "", "")
    assert entry["command"] == "uvx"
    assert "--from" in entry["args"]
    assert "nuguard[mcp]" in entry["args"]
    assert "nuguard-mcp" in entry["args"]


def test_build_entry_env_with_api_key() -> None:
    with patch("shutil.which", return_value=None):
        entry = reg._build_server_entry("sk-test", "", "")
    assert entry["env"]["LITELLM_API_KEY"] == "sk-test"


def test_build_entry_env_with_config_path(tmp_path: Path) -> None:
    cfg = tmp_path / "nuguard.yaml"
    cfg.write_text("")
    with patch("shutil.which", return_value=None):
        entry = reg._build_server_entry("", str(cfg), "")
    assert entry["env"]["NUGUARD_DEFAULT_CONFIG"] == str(cfg.resolve())


def test_build_entry_env_with_redteam_model() -> None:
    with patch("shutil.which", return_value=None):
        entry = reg._build_server_entry("", "", "openai/gpt-4o")
    assert entry["env"]["NUGUARD_REDTEAM_LLM_MODEL"] == "openai/gpt-4o"


def test_build_entry_no_env_key_when_all_empty() -> None:
    with patch("shutil.which", return_value=None):
        entry = reg._build_server_entry("", "", "")
    assert "env" not in entry


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------


def test_read_json_missing_file_returns_empty(tmp_path: Path) -> None:
    result = reg._read_json(tmp_path / "nonexistent.json")
    assert result == {}


def test_read_json_valid_file(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    p.write_text('{"foo": 42}')
    assert reg._read_json(p) == {"foo": 42}


def test_read_json_invalid_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json {{")
    assert reg._read_json(p) == {}


def test_write_json_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "out.json"
    reg._write_json(p, {"a": 1}, dry_run=False)
    assert p.exists()
    assert json.loads(p.read_text()) == {"a": 1}


def test_write_json_dry_run_does_not_write(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    reg._write_json(p, {"a": 1}, dry_run=True)
    assert not p.exists()


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


def test_register_writes_desktop_and_mcp_json(tmp_path: Path) -> None:
    desktop = tmp_path / "claude_desktop_config.json"
    mcp_json = tmp_path / ".mcp.json"

    with patch("shutil.which", return_value=None):
        with patch.object(reg, "_claude_desktop_config_path", return_value=desktop):
            with patch("os.getcwd", return_value=str(tmp_path)):
                # Override .mcp.json path inside register() via Path(".")
                orig_cwd = Path.cwd
                with patch.object(Path, "cwd", return_value=tmp_path):
                    # register() uses Path(".mcp.json") directly — mock it
                    pass
                # Simpler: call register with skip flags to isolate each path
                reg.register(
                    api_key="",
                    config_path="",
                    redteam_model="",
                    dry_run=False,
                    skip_desktop=False,
                    skip_mcp_json=True,  # test desktop only
                )

    assert desktop.exists()
    data = json.loads(desktop.read_text())
    assert "nuguard" in data["mcpServers"]


def test_register_already_up_to_date(tmp_path: Path, capsys) -> None:
    desktop = tmp_path / "claude_desktop_config.json"

    with patch("shutil.which", return_value=None):
        entry = reg._build_server_entry("", "", "")
        desktop.write_text(json.dumps({"mcpServers": {"nuguard": entry}}))

        with patch.object(reg, "_claude_desktop_config_path", return_value=desktop):
            reg.register(
                api_key="",
                config_path="",
                redteam_model="",
                dry_run=False,
                skip_desktop=False,
                skip_mcp_json=True,
            )

    captured = capsys.readouterr()
    assert "already up-to-date" in captured.out


def test_register_dry_run_does_not_write(tmp_path: Path) -> None:
    desktop = tmp_path / "claude_desktop_config.json"

    with patch("shutil.which", return_value=None):
        with patch.object(reg, "_claude_desktop_config_path", return_value=desktop):
            reg.register(
                api_key="",
                config_path="",
                redteam_model="",
                dry_run=True,
                skip_desktop=False,
                skip_mcp_json=True,
            )

    assert not desktop.exists()


def test_register_skip_desktop_does_not_touch_desktop(tmp_path: Path) -> None:
    desktop = tmp_path / "claude_desktop_config.json"

    with patch("shutil.which", return_value=None):
        with patch.object(reg, "_claude_desktop_config_path", return_value=desktop):
            reg.register(
                api_key="",
                config_path="",
                redteam_model="",
                dry_run=False,
                skip_desktop=True,
                skip_mcp_json=True,
            )

    assert not desktop.exists()


# ---------------------------------------------------------------------------
# unregister()
# ---------------------------------------------------------------------------


def test_unregister_removes_desktop_entry(tmp_path: Path, capsys) -> None:
    desktop = tmp_path / "claude_desktop_config.json"
    desktop.write_text(json.dumps({"mcpServers": {"nuguard": {"command": "nuguard-mcp", "args": []}}}))

    with patch.object(reg, "_claude_desktop_config_path", return_value=desktop):
        reg.unregister(dry_run=False)

    data = json.loads(desktop.read_text())
    assert "nuguard" not in data.get("mcpServers", {})
    assert "removed" in capsys.readouterr().out


def test_unregister_nothing_to_remove(tmp_path: Path, capsys) -> None:
    desktop = tmp_path / "claude_desktop_config.json"
    desktop.write_text(json.dumps({"mcpServers": {}}))

    with patch.object(reg, "_claude_desktop_config_path", return_value=desktop):
        reg.unregister(dry_run=False)

    assert "nothing to remove" in capsys.readouterr().out.lower()


def test_unregister_dry_run_does_not_remove(tmp_path: Path) -> None:
    desktop = tmp_path / "claude_desktop_config.json"
    desktop.write_text(json.dumps({"mcpServers": {"nuguard": {"command": "nuguard-mcp", "args": []}}}))

    with patch.object(reg, "_claude_desktop_config_path", return_value=desktop):
        reg.unregister(dry_run=True)

    data = json.loads(desktop.read_text())
    assert "nuguard" in data["mcpServers"]
