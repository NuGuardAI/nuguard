"""Provider-style Go Semgrep E2E via SemgrepScannerPlugin.

Exercises the NuGuard Semgrep integration path against minimal Azure / AWS /
GCP / OpenAI / Claude flavoured fixtures under
``fixtures/go_provider_samples/``.

This is intentionally *not* a direct ``semgrep`` CLI invocation (see
``test_semgrep_go_rules.py`` for rule-behaviour line tests). Official
first-party SDK sink shapes are tracked in issue #223 and are out of scope.

Semgrep's default ``.semgrepignore`` skips paths under ``tests/``, and the
plugin scans directories (not explicit file args). Fixtures are therefore
materialized into a temporary directory before
``SemgrepScannerPlugin.run`` so discovery matches production source trees
that are not nested under a ``tests/`` path segment.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType

import pytest

from nuguard.analysis.plugins.semgrep_scanner import SemgrepScannerPlugin

_FIXTURES = Path(__file__).parent / "fixtures" / "go_provider_samples"

_GO_RULE_IDS = frozenset(
    {
        "nuguard-go-llm-prompt-injection-sprintf",
        "nuguard-go-hardcoded-api-key",
        "nuguard-go-llm-missing-context-timeout",
        "nuguard-go-llm-insecure-tls",
    }
)

# Expected rule-ID sets per provider (normalized Semgrep check_id suffixes).
_EXPECTED_BY_PROVIDER: dict[str, frozenset[str]] = {
    "openai": frozenset(
        {
            "nuguard-go-llm-prompt-injection-sprintf",
            "nuguard-go-hardcoded-api-key",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
    "azure": frozenset(
        {
            "nuguard-go-llm-insecure-tls",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
    "aws": frozenset(
        {
            "nuguard-go-llm-prompt-injection-sprintf",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
    "gcp": frozenset(
        {
            "nuguard-go-hardcoded-api-key",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
    "claude": frozenset(
        {
            "nuguard-go-hardcoded-api-key",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
}


def _semgrep_binary() -> str | None:
    return shutil.which("semgrep")


def _require_semgrep() -> None:
    if _semgrep_binary() is not None:
        return
    if os.environ.get("CI"):
        pytest.fail(
            "semgrep is required in CI for Go provider Semgrep E2E tests; "
            "install semgrep before running pytest"
        )
    pytest.skip("semgrep not installed — Go provider Semgrep E2E skipped")


def _normalize_rule_id(check_id: str) -> str:
    """Strip Semgrep config-path prefixes from a check_id."""
    return str(check_id).rsplit(".", 1)[-1]


@contextmanager
def _materialize_provider(provider: str) -> Iterator[Path]:
    """Copy one provider fixture into a temp dir suitable for Semgrep directory scans."""
    source = _FIXTURES / provider
    assert source.is_dir(), f"missing provider fixture directory: {source}"
    go_files = sorted(source.glob("*.go"))
    assert go_files, f"no Go fixtures in {source}"

    temp_dir = Path(tempfile.mkdtemp(prefix=f"nuguard-go-provider-{provider}-"))
    try:
        for path in go_files:
            shutil.copy2(path, temp_dir / path.name)
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _scan_provider(provider: str) -> frozenset[str]:
    """Run SemgrepScannerPlugin on one provider fixture (via temp materialization)."""
    with _materialize_provider(provider) as source_dir:
        result = SemgrepScannerPlugin().run(
            {"nodes": []},
            {
                "source_path": str(source_dir),
                "semgrep_exclude_tests": False,
                "semgrep_timeout": 120.0,
            },
        )
        assert result.status != "skipped", (
            f"SemgrepScannerPlugin skipped scan of {provider}: {result.message}"
        )
        assert result.plugin == "semgrep"

        fired: set[str] = set()
        for finding in result.findings:
            rule_id = _normalize_rule_id(str(finding.get("rule_id", "")))
            if rule_id:
                fired.add(rule_id)
        return frozenset(fired)


@pytest.fixture(scope="module")
def provider_semgrep_findings() -> Mapping[str, frozenset[str]]:
    """Scan each provider once; share immutable results across this module's tests."""
    _require_semgrep()
    findings = {
        provider: _scan_provider(provider) for provider in sorted(_EXPECTED_BY_PROVIDER)
    }
    return MappingProxyType(findings)


@pytest.mark.parametrize("provider", sorted(_EXPECTED_BY_PROVIDER))
def test_provider_fixture_emits_expected_go_rules(
    provider: str,
    provider_semgrep_findings: Mapping[str, frozenset[str]],
) -> None:
    """Each provider sample must surface its expected Go AI-security rule IDs."""
    expected = _EXPECTED_BY_PROVIDER[provider]
    fired = provider_semgrep_findings[provider]
    missing = expected - fired
    assert not missing, (
        f"{provider}: missing expected Go rule IDs {sorted(missing)}; "
        f"fired={sorted(fired)}"
    )


def test_provider_pack_covers_all_go_rules(
    provider_semgrep_findings: Mapping[str, frozenset[str]],
) -> None:
    """Collectively, the provider pack must exercise all four Go rule IDs."""
    fired: set[str] = set()
    for rule_ids in provider_semgrep_findings.values():
        fired |= rule_ids

    missing = _GO_RULE_IDS - fired
    assert not missing, (
        f"provider pack missing Go rule coverage {sorted(missing)}; "
        f"fired={sorted(fired)}"
    )
    assert fired & _GO_RULE_IDS == _GO_RULE_IDS
