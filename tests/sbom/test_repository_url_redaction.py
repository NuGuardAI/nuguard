from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from nuguard.common.url_sanitization import sanitize_repository_url
from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.generator import SbomGenerator
from nuguard.sbom.models import AiSbomDocument
from nuguard.sbom.public_api import SbomGenerateRequest, generate_sbom

_TOKEN = "ghp_super_secret_value"
_AUTHENTICATED_URL = f"https://x-access-token:{_TOKEN}@github.com/org/repo.git"
_DISPLAY_URL = "https://github.com/org/repo.git"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (_AUTHENTICATED_URL, _DISPLAY_URL),
        (
            f"https://github.com/org/repo.git?ref=main&access_token={_TOKEN}",
            "https://github.com/org/repo.git?ref=main&access_token=REDACTED",
        ),
        (_DISPLAY_URL, _DISPLAY_URL),
    ],
)
def test_sanitize_repository_url(url: str, expected: str) -> None:
    assert sanitize_repository_url(url) == expected


def test_generator_uses_sanitized_source_ref() -> None:
    generator = SbomGenerator()
    received: dict[str, str | None] = {}

    def fake_extract(
        url: str,
        ref: str,
        config: AiSbomConfig,
        cache_dir: str | Path | None = None,
        source_ref: str | None = None,
    ) -> AiSbomDocument:
        received.update(url=url, source_ref=source_ref)
        return AiSbomDocument(target=source_ref or url)

    generator._extractor.extract_from_repo = fake_extract  # type: ignore[method-assign]
    result = generator.from_repo(_AUTHENTICATED_URL)

    assert received == {"url": _AUTHENTICATED_URL, "source_ref": _DISPLAY_URL}
    assert result.target == _DISPLAY_URL
    assert _TOKEN not in result.model_dump_json()


def test_repository_cache_name_uses_url_path_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    extractor = AiSbomExtractor()
    clone_destinations: list[Path] = []

    monkeypatch.setattr(
        extractor,
        "_clone_repo",
        lambda *, url, ref, dest: clone_destinations.append(dest),
    )
    monkeypatch.setattr(
        extractor,
        "extract_from_path",
        lambda path, config, **kwargs: AiSbomDocument(target=kwargs["source_ref"]),
    )

    result = extractor.extract_from_repo(
        f"{_AUTHENTICATED_URL}?ref=main&access_token={_TOKEN}",
        "main",
        AiSbomConfig(),
        cache_dir=tmp_path,
    )

    assert clone_destinations == [tmp_path / "repo" / "repo"]
    assert result.target.endswith("?ref=main&access_token=REDACTED")
    assert _TOKEN not in result.model_dump_json()


@pytest.mark.asyncio
async def test_public_result_uses_sanitized_source_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_from_repo(
        self: SbomGenerator,
        url: str,
        ref: str = "main",
        output: Path | None = None,
    ) -> AiSbomDocument:
        return AiSbomDocument(target=sanitize_repository_url(url))

    monkeypatch.setattr(SbomGenerator, "from_repo", fake_from_repo)
    result = await generate_sbom(SbomGenerateRequest(repo_url=_AUTHENTICATED_URL))

    assert result.source_ref == _DISPLAY_URL
    assert result.sbom.target == _DISPLAY_URL
    assert _TOKEN not in result.model_dump_json()


def test_clone_failure_redacts_url_logs_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    stderr = f"fatal: unable to access '{_AUTHENTICATED_URL}': denied".encode()

    def fail_clone(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(128, "git", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fail_clone)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError) as exc_info:
        AiSbomExtractor._clone_repo(_AUTHENTICATED_URL, "main", tmp_path / "repo")

    assert _TOKEN not in str(exc_info.value)
    assert _DISPLAY_URL in str(exc_info.value)
    assert _TOKEN not in caplog.text