#!/usr/bin/env python3
"""Run a NuGuard E2E redteam scan against any benchmark fixture app.

Usage
-----
  # Interactive picker
  python tests/run_redteam.py

  # Pick by name directly
  python tests/run_redteam.py --app healthcare-voice-agent

  # List available apps
  python tests/run_redteam.py --list

  # Pass extra pytest options (e.g. -s for live output)
  python tests/run_redteam.py --app healthcare-voice-agent -- -s -v

Required env vars depend on the chosen fixture (shown in the picker).
Secrets are never stored here — export them before running:

  export OPENAI_API_KEY="sk-..."
  export GEMINI_API_KEY="..."    # NuGuard scanner LLM (optional; skips LLM enrichment if absent)
  export DATABASE_URL="postgresql://..."  # healthcare-voice-agent DB endpoints (optional)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Make sure we can import from the project root regardless of cwd
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from tests.redteam.app_runner import APP_CONFIGS  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD  = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED   = "\033[31m"
_CYAN  = "\033[36m"
_DIM   = "\033[2m"


def _c(color: str, text: str) -> str:
    """Wrap text in ANSI color if stdout is a TTY."""
    if sys.stdout.isatty():
        return f"{color}{text}{_RESET}"
    return text


def _check_env_vars(app_name: str) -> tuple[list[str], list[str]]:
    """Return (missing_required, missing_optional) env var names."""
    cfg = APP_CONFIGS[app_name]
    missing_req = [v for v in cfg.required_env_vars if not os.getenv(v)]
    missing_opt = [v for v in cfg.optional_env_vars if not os.getenv(v)]
    return missing_req, missing_opt


def _print_app_list() -> None:
    print(_c(_BOLD, "\nAvailable fixture apps:\n"))
    for i, (key, cfg) in enumerate(APP_CONFIGS.items(), 1):
        req = ", ".join(cfg.required_env_vars) if cfg.required_env_vars else "none"
        print(f"  {_c(_CYAN, str(i))}. {_c(_BOLD, key)}")
        if cfg.notes:
            print(f"     {_c(_DIM, cfg.notes)}")
        print(f"     Required env vars: {_c(_YELLOW, req)}")
        if cfg.optional_env_vars:
            opt_str = ", ".join(cfg.optional_env_vars)
            print(f"     Optional env vars: {_c(_DIM, opt_str)}")
        print()


def _pick_app_interactive() -> str:
    """Show a numbered menu and return the chosen app key."""
    keys = list(APP_CONFIGS.keys())
    _print_app_list()
    while True:
        try:
            raw = input(_c(_BOLD, "Enter app number or name: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if raw in APP_CONFIGS:
            return raw
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(keys):
                return keys[idx]
        except ValueError:
            pass

        # Partial-name match
        matches = [k for k in keys if raw.lower() in k.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(_c(_YELLOW, f"Ambiguous — matches: {', '.join(matches)}. Please be more specific.\n"))
        else:
            print(_c(_RED, f"No match for '{raw}'. Try a number (1–{len(keys)}) or an exact name.\n"))


def _run_test(app_name: str, extra_pytest_args: list[str]) -> int:
    """Run the pytest E2E test for *app_name* and return the exit code."""
    cfg = APP_CONFIGS[app_name]
    missing_req, missing_opt = _check_env_vars(app_name)

    if missing_req:
        print(_c(_RED, f"\n✗ Missing required environment variable(s): {', '.join(missing_req)}"))
        print(_c(_YELLOW, "  Export them and re-run:\n"))
        for v in missing_req:
            print(f"    export {v}=\"...\"")
        print()
        return 1

    if missing_opt:
        print(_c(_YELLOW, f"\n⚠  Optional env var(s) not set: {', '.join(missing_opt)}"))
        for v, desc in cfg.optional_env_vars.items():
            if not os.getenv(v):
                print(f"   {v}: {desc}")
        print(_c(_DIM, "  Some features may be degraded — continuing anyway.\n"))

    # Derive the test node ID from the app name
    test_id = f"tests/redteam/test_e2e_redteam.py::test_e2e_{app_name.replace('-', '_')}"

    cmd = [
        sys.executable, "-m", "pytest",
        test_id,
        "-v",
        *extra_pytest_args,
    ]

    env = os.environ.copy()
    env["NUGUARD_REDTEAM_E2E"] = "1"

    print(_c(_BOLD, f"\n▶  Running: {' '.join(cmd)}\n"))
    result = subprocess.run(cmd, env=env, cwd=str(_REPO_ROOT))

    # Show path to the latest report
    output_dir = _REPO_ROOT / "tests" / "output"
    reports = sorted(
        output_dir.glob(f"redteam_{app_name}_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if reports:
        print(_c(_GREEN, f"\n📄 Report: {reports[0]}"))

    return result.returncode


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a NuGuard E2E redteam scan against a benchmark fixture app.",
        epilog="Extra arguments after '--' are forwarded to pytest (e.g. -- -s --tb=short).",
    )
    parser.add_argument(
        "--app", "-a",
        metavar="NAME",
        help="App name to scan (skip interactive picker). Use --list to see options.",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available apps and exit.",
    )

    # Split args at '--' so everything after is passed to pytest
    argv = sys.argv[1:]
    split_at = argv.index("--") if "--" in argv else len(argv)
    our_argv = argv[:split_at]
    pytest_extra = argv[split_at + 1:] if split_at < len(argv) else []

    args = parser.parse_args(our_argv)

    if args.list:
        _print_app_list()
        sys.exit(0)

    app_name = args.app
    if app_name:
        if app_name not in APP_CONFIGS:
            # Try partial match
            matches = [k for k in APP_CONFIGS if args.app.lower() in k.lower()]
            if len(matches) == 1:
                app_name = matches[0]
            else:
                print(_c(_RED, f"Unknown app '{args.app}'."))
                _print_app_list()
                sys.exit(1)
    else:
        app_name = _pick_app_interactive()

    print(_c(_BOLD, f"\nSelected: {_c(_CYAN, app_name)}"))
    sys.exit(_run_test(app_name, pytest_extra))


if __name__ == "__main__":
    main()
