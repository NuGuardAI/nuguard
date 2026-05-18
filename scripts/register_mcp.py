#!/usr/bin/env python3
"""
register_mcp.py — Register nuguard-mcp with Claude Desktop and Claude Code.

Writes the nuguard MCP server entry into:
  • ~/Library/Application Support/Claude/claude_desktop_config.json  (macOS)
  • %APPDATA%/Claude/claude_desktop_config.json                      (Windows)
  • ~/.config/Claude/claude_desktop_config.json                      (Linux)
  • .mcp.json in the current working directory                        (Claude Code)

Usage:
    python scripts/register_mcp.py
    python scripts/register_mcp.py --api-key sk-...
    python scripts/register_mcp.py --config /path/to/nuguard.yaml
    python scripts/register_mcp.py --dry-run
    python scripts/register_mcp.py --unregister
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVER_NAME = "nuguard"
PACKAGE_SPEC = "nuguard[mcp]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _claude_desktop_config_path() -> Path | None:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA") or ""
        if not appdata:
            return None
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    # Linux / other
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "Claude" / "claude_desktop_config.json"


def _build_server_entry(api_key: str, config_path: str, redteam_model: str) -> dict:
    """Build the mcpServers entry for nuguard."""
    if shutil.which("nuguard-mcp"):
        command, args = "nuguard-mcp", []
    else:
        command, args = "uvx", ["--from", PACKAGE_SPEC, "nuguard-mcp"]

    env: dict[str, str] = {}
    if api_key:
        env["LITELLM_API_KEY"] = api_key
    if config_path:
        env["NUGUARD_DEFAULT_CONFIG"] = str(Path(config_path).resolve())
    if redteam_model:
        env["NUGUARD_REDTEAM_LLM_MODEL"] = redteam_model

    entry: dict = {"command": command, "args": args}
    if env:
        entry["env"] = env
    return entry


def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_json(path: Path, data: dict, dry_run: bool) -> None:
    text = json.dumps(data, indent=2) + "\n"
    if dry_run:
        print(f"  [dry-run] would write {path}:")
        for line in text.splitlines():
            print(f"    {line}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Registration actions
# ---------------------------------------------------------------------------

def register(
    api_key: str,
    config_path: str,
    redteam_model: str,
    dry_run: bool,
    skip_desktop: bool,
    skip_mcp_json: bool,
) -> int:
    entry = _build_server_entry(api_key, config_path, redteam_model)
    launch = f"{entry['command']} {' '.join(entry['args'])}".strip()
    changed = False

    # ── Claude Desktop ────────────────────────────────────────────────────
    if not skip_desktop:
        desktop_path = _claude_desktop_config_path()
        if desktop_path is None:
            print("  Claude Desktop: unsupported platform — skipped")
        else:
            config = _read_json(desktop_path)
            existing = config.get("mcpServers", {}).get(SERVER_NAME)
            if existing == entry:
                print(f"  Claude Desktop: already up-to-date ({desktop_path})")
            else:
                config.setdefault("mcpServers", {})[SERVER_NAME] = entry
                _write_json(desktop_path, config, dry_run)
                verb = "would update" if dry_run else "updated"
                print(f"  Claude Desktop: {verb} → {desktop_path}")
                print(f"    launch: {launch}")
                changed = True

            if not dry_run:
                print("  Restart Claude Desktop to activate the new server.")

    # ── Claude Code (.mcp.json) ───────────────────────────────────────────
    if not skip_mcp_json:
        mcp_path = Path(".mcp.json")
        mcp_config = _read_json(mcp_path)
        existing = mcp_config.get("mcpServers", {}).get(SERVER_NAME)
        if existing == entry:
            print(f"  Claude Code .mcp.json: already up-to-date ({mcp_path.resolve()})")
        else:
            mcp_config.setdefault("mcpServers", {})[SERVER_NAME] = entry
            _write_json(mcp_path, mcp_config, dry_run)
            verb = "would write" if dry_run else "wrote"
            print(f"  Claude Code .mcp.json: {verb} → {mcp_path.resolve()}")
            changed = True

    if not changed:
        print("Nothing to do — all targets already up-to-date.")

    _print_env_hints(api_key, config_path, redteam_model)
    return 0


def unregister(dry_run: bool) -> int:
    removed = False

    desktop_path = _claude_desktop_config_path()
    if desktop_path and desktop_path.exists():
        config = _read_json(desktop_path)
        if SERVER_NAME in config.get("mcpServers", {}):
            del config["mcpServers"][SERVER_NAME]
            _write_json(desktop_path, config, dry_run)
            verb = "would remove" if dry_run else "removed"
            print(f"  Claude Desktop: {verb} '{SERVER_NAME}' from {desktop_path}")
            removed = True

    mcp_path = Path(".mcp.json")
    if mcp_path.exists():
        mcp_config = _read_json(mcp_path)
        if SERVER_NAME in mcp_config.get("mcpServers", {}):
            del mcp_config["mcpServers"][SERVER_NAME]
            _write_json(mcp_path, mcp_config, dry_run)
            verb = "would remove" if dry_run else "removed"
            print(f"  Claude Code .mcp.json: {verb} '{SERVER_NAME}'")
            removed = True

    if not removed:
        print(f"'{SERVER_NAME}' was not registered in any target — nothing to remove.")
    return 0


def _print_env_hints(api_key: str, config_path: str, redteam_model: str) -> None:
    missing = []
    if not api_key and not os.environ.get("LITELLM_API_KEY"):
        missing.append("LITELLM_API_KEY          (LLM-enriched analysis — Gemini/OpenAI/Anthropic)")
    if not config_path and not os.environ.get("NUGUARD_DEFAULT_CONFIG"):
        missing.append("NUGUARD_DEFAULT_CONFIG    (path to nuguard.yaml — skips config_path on every call)")
    if not redteam_model and not os.environ.get("NUGUARD_REDTEAM_LLM_MODEL"):
        missing.append("NUGUARD_REDTEAM_LLM_MODEL (e.g. openai/gpt-4o — required for nuguard_redteam)")

    if missing:
        print()
        print("Optional environment variables not set:")
        for var in missing:
            print(f"  {var}")
        print()
        print("Pass them via --api-key / --config / --redteam-model, or set them in")
        print("the 'env' block of claude_desktop_config.json / .mcp.json manually.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Register or unregister the nuguard MCP server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--api-key",
        metavar="KEY",
        default=os.environ.get("LITELLM_API_KEY", ""),
        help="LITELLM_API_KEY to embed in the server env (default: $LITELLM_API_KEY)",
    )
    p.add_argument(
        "--config",
        metavar="PATH",
        default=os.environ.get("NUGUARD_DEFAULT_CONFIG", ""),
        help="Absolute path to nuguard.yaml (default: $NUGUARD_DEFAULT_CONFIG)",
    )
    p.add_argument(
        "--redteam-model",
        metavar="MODEL",
        default=os.environ.get("NUGUARD_REDTEAM_LLM_MODEL", ""),
        help="LiteLLM model string for red-team payload generation",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without changing any files",
    )
    p.add_argument(
        "--unregister",
        action="store_true",
        help="Remove the nuguard entry from all targets",
    )
    p.add_argument(
        "--skip-desktop",
        action="store_true",
        help="Skip updating claude_desktop_config.json",
    )
    p.add_argument(
        "--skip-mcp-json",
        action="store_true",
        help="Skip writing .mcp.json in the current directory",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    print(f"nuguard MCP registration {'(dry run) ' if args.dry_run else ''}—")

    if args.unregister:
        return unregister(dry_run=args.dry_run)

    return register(
        api_key=args.api_key,
        config_path=args.config,
        redteam_model=args.redteam_model,
        dry_run=args.dry_run,
        skip_desktop=args.skip_desktop,
        skip_mcp_json=args.skip_mcp_json,
    )


if __name__ == "__main__":
    sys.exit(main())
