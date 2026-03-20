"""Tests for the xelo CLI argument parser."""

from __future__ import annotations

import pytest


def _parse(argv: list[str]) -> object:
    """Helper: parse args using main's parser logic by monkey-patching sys.argv."""
    import argparse

    # Build the same parser that main() builds, then parse
    from xelo.cli import _add_llm_args

    parser = argparse.ArgumentParser(prog="xelo")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--debug", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_p = subparsers.add_parser("scan")
    scan_p.add_argument("target", metavar="<path|url>")
    scan_p.add_argument("--ref", default="main")
    scan_p.add_argument("--token", default=None)
    scan_p.add_argument(
        "--format", choices=["json", "cyclonedx", "cyclonedx-ext", "spdx"], default="json"
    )
    scan_p.add_argument("--output", default="-")
    _add_llm_args(scan_p)

    schema_p = subparsers.add_parser("schema")
    schema_p.add_argument("--output", default="-")

    validate_p = subparsers.add_parser("validate")
    validate_p.add_argument("input")

    return parser.parse_args(argv)


def test_scan_cyclonedx_ext_parses() -> None:
    args = _parse(["scan", "./repo", "--format", "cyclonedx-ext", "--output", "sbom-ext.cdx.json"])
    assert args.format == "cyclonedx-ext"
    assert args.output == "sbom-ext.cdx.json"


def test_scan_defaults_to_stdout() -> None:
    args = _parse(["scan", "./repo"])
    assert args.output == "-"


def test_scan_rejects_unknown_format() -> None:
    with pytest.raises(SystemExit):
        _parse(["scan", "./repo", "--format", "unknown"])


def test_scan_rejects_cdx_bom_flag() -> None:
    with pytest.raises(SystemExit):
        _parse(["scan", "./repo", "--format", "cyclonedx-ext", "--cdx-bom", "standard-bom.json"])


def test_scan_rejects_enable_llm_flag() -> None:
    """Old --enable-llm flag is gone; --llm is the new flag."""
    with pytest.raises(SystemExit):
        _parse(["scan", "./repo", "--enable-llm"])


def test_scan_accepts_llm_flag() -> None:
    args = _parse(["scan", "./repo", "--llm"])
    assert args.llm is True


def test_scan_rejects_deterministic_only_flag() -> None:
    with pytest.raises(SystemExit):
        _parse(["scan", "./repo", "--deterministic-only"])


def test_schema_defaults_to_stdout() -> None:
    args = _parse(["schema"])
    assert args.output == "-"


def test_schema_accepts_output_file() -> None:
    args = _parse(["schema", "--output", "schema.json"])
    assert args.output == "schema.json"


def test_validate_requires_input() -> None:
    args = _parse(["validate", "sbom.json"])
    assert args.input == "sbom.json"


# ── Token flag tests ──────────────────────────────────────────────────────────


def test_scan_accepts_token_flag() -> None:
    args = _parse(["scan", "https://github.com/org/repo", "--token", "ghp_abc123"])
    assert args.token == "ghp_abc123"


def test_scan_token_defaults_to_none() -> None:
    args = _parse(["scan", "./repo"])
    assert args.token is None


def test_inject_token_https() -> None:
    from xelo.cli import _inject_token

    result = _inject_token("https://github.com/org/repo.git", "ghp_abc")
    assert result == "https://ghp_abc@github.com/org/repo.git"


def test_inject_token_preserves_non_https() -> None:
    from xelo.cli import _inject_token

    # SSH URLs should not be modified
    url = "git@github.com:org/repo.git"
    assert _inject_token(url, "ghp_abc") == url


def test_inject_token_preserves_port() -> None:
    from xelo.cli import _inject_token

    result = _inject_token("https://git.example.com:8443/org/repo.git", "tok")
    assert result == "https://tok@git.example.com:8443/org/repo.git"


def test_resolve_token_prefers_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from argparse import Namespace

    from xelo.cli import _resolve_token

    monkeypatch.setenv("GH_TOKEN", "env_token")
    args = Namespace(token="flag_token")
    assert _resolve_token(args) == "flag_token"


def test_resolve_token_falls_back_to_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from argparse import Namespace

    from xelo.cli import _resolve_token

    monkeypatch.setenv("GH_TOKEN", "env_token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    args = Namespace(token=None)
    assert _resolve_token(args) == "env_token"


def test_resolve_token_falls_back_to_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from argparse import Namespace

    from xelo.cli import _resolve_token

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "gh_env")
    args = Namespace(token=None)
    assert _resolve_token(args) == "gh_env"


def test_resolve_token_returns_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from argparse import Namespace

    from xelo.cli import _resolve_token

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    args = Namespace(token=None)
    assert _resolve_token(args) is None
