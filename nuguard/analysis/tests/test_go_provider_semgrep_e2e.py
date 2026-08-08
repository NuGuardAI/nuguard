"""Provider-style Go Semgrep E2E via SemgrepScannerPlugin.

Exercises the NuGuard Semgrep integration path against minimal Azure / AWS /
GCP / OpenAI / Claude flavoured fixtures under
``fixtures/go_provider_samples/``.

This is intentionally *not* a direct ``semgrep`` CLI invocation (see
``test_semgrep_go_rules.py`` for rule-behaviour line tests). Each provider
directory covers community/compat shapes (``vulnerable.go``, PR #224) and
official non-streaming SDK call shapes (``official_vulnerable.go``, #223).

``SemgrepScannerPlugin`` adds ``--exclude tests/`` by default when
``semgrep_exclude_tests`` is true, and the plugin scans directories (not
explicit file args). Fixtures under this test tree are therefore materialized
into a temporary source-like directory before ``SemgrepScannerPlugin.run`` so
discovery matches production source trees that are not nested under a
``tests/`` path segment. E2E cases pass ``semgrep_exclude_tests=False`` on the
materialized temp dir as belt-and-suspenders.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import defaultdict
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
# These #224 community/compat expectations remain the regression baseline.
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

# Official (#223) fixtures must themselves produce at least these rule IDs.
# Kept distinct from #224 so community findings cannot satisfy official coverage.
_EXPECTED_OFFICIAL_BY_PROVIDER: dict[str, frozenset[str]] = {
    "openai": frozenset(
        {
            "nuguard-go-llm-prompt-injection-sprintf",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
    "azure": frozenset(
        {
            "nuguard-go-llm-prompt-injection-sprintf",
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
            "nuguard-go-llm-prompt-injection-sprintf",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
    "claude": frozenset(
        {
            "nuguard-go-llm-prompt-injection-sprintf",
            "nuguard-go-llm-missing-context-timeout",
        }
    ),
}

_OFFICIAL_FIXTURE_NAME = "official_vulnerable.go"
_COMMUNITY_FIXTURE_NAME = "vulnerable.go"


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


def _finding_filename(finding: Mapping[str, object]) -> str:
    """Best-effort filename from SemgrepScannerPlugin finding ``affected`` paths."""
    affected = finding.get("affected") or []
    if not isinstance(affected, list) or not affected:
        return ""
    location = str(affected[0])
    # location is typically "<path>:<line>"
    path_part = location.rsplit(":", 1)[0]
    return Path(path_part).name


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


def _scan_provider(provider: str) -> Mapping[str, frozenset[str]]:
    """Run SemgrepScannerPlugin; return rule IDs keyed by fixture filename."""
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

        by_file: dict[str, set[str]] = defaultdict(set)
        for finding in result.findings:
            rule_id = _normalize_rule_id(str(finding.get("rule_id", "")))
            if not rule_id:
                continue
            filename = _finding_filename(finding)
            if filename:
                by_file[filename].add(rule_id)
        return MappingProxyType({name: frozenset(ids) for name, ids in by_file.items()})


@pytest.fixture(scope="module")
def provider_semgrep_findings_by_file() -> Mapping[str, Mapping[str, frozenset[str]]]:
    """Scan each provider once; share immutable per-file results across tests."""
    _require_semgrep()
    findings = {
        provider: _scan_provider(provider)
        for provider in sorted(_EXPECTED_BY_PROVIDER)
    }
    return MappingProxyType(findings)


def _provider_union(
    by_file: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    fired: set[str] = set()
    for rule_ids in by_file.values():
        fired |= set(rule_ids)
    return frozenset(fired)


@pytest.mark.parametrize("provider", sorted(_EXPECTED_BY_PROVIDER))
def test_provider_fixture_emits_expected_go_rules(
    provider: str,
    provider_semgrep_findings_by_file: Mapping[str, Mapping[str, frozenset[str]]],
) -> None:
    """Each provider sample must surface its expected Go AI-security rule IDs."""
    expected = _EXPECTED_BY_PROVIDER[provider]
    fired = _provider_union(provider_semgrep_findings_by_file[provider])
    missing = expected - fired
    assert not missing, (
        f"{provider}: missing expected Go rule IDs {sorted(missing)}; "
        f"fired={sorted(fired)}"
    )


@pytest.mark.parametrize("provider", sorted(_EXPECTED_BY_PROVIDER))
def test_community_vulnerable_fixture_still_fires(
    provider: str,
    provider_semgrep_findings_by_file: Mapping[str, Mapping[str, frozenset[str]]],
) -> None:
    """#224 regression: community ``vulnerable.go`` must satisfy expected rule IDs."""
    expected = _EXPECTED_BY_PROVIDER[provider]
    by_file = provider_semgrep_findings_by_file[provider]
    fired = by_file.get(_COMMUNITY_FIXTURE_NAME, frozenset())
    missing = expected - fired
    assert not missing, (
        f"{provider}: {_COMMUNITY_FIXTURE_NAME} missing expected Go rule IDs "
        f"{sorted(missing)}; fired={sorted(fired)}"
    )


@pytest.mark.parametrize("provider", sorted(_EXPECTED_OFFICIAL_BY_PROVIDER))
def test_official_vulnerable_fixture_emits_expected_go_rules(
    provider: str,
    provider_semgrep_findings_by_file: Mapping[str, Mapping[str, frozenset[str]]],
) -> None:
    """#223: official_vulnerable.go must itself fire expected rule IDs."""
    expected = _EXPECTED_OFFICIAL_BY_PROVIDER[provider]
    by_file = provider_semgrep_findings_by_file[provider]
    fired = by_file.get(_OFFICIAL_FIXTURE_NAME, frozenset())
    missing = expected - fired
    assert not missing, (
        f"{provider}: official fixture missing rule IDs {sorted(missing)}; "
        f"fired={sorted(fired)}; files={sorted(by_file)}"
    )


def test_provider_pack_covers_all_go_rules(
    provider_semgrep_findings_by_file: Mapping[str, Mapping[str, frozenset[str]]],
) -> None:
    """Collectively, the provider pack must exercise all four Go rule IDs."""
    fired: set[str] = set()
    for by_file in provider_semgrep_findings_by_file.values():
        fired |= set(_provider_union(by_file))

    missing = _GO_RULE_IDS - fired
    assert not missing, (
        f"provider pack missing Go rule coverage {sorted(missing)}; "
        f"fired={sorted(fired)}"
    )
    assert fired & _GO_RULE_IDS == _GO_RULE_IDS
