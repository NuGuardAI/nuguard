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
    assert result.local_cloned_path == str((tmp_path / "repo" / "repo").resolve())
    assert result.target.endswith("?ref=main&access_token=REDACTED")
    assert _TOKEN not in result.model_dump_json()


def test_extract_from_repo_resolves_credentialed_subfolder_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression test for the ai-asset-service bug: a client calling the
    frozen ``extract_from_repo`` contract directly with a token-embedded
    ``/tree/<ref>/<subpath>`` URL (as opposed to a plain repo-root URL) must
    resolve to the subfolder clone path instead of a doomed ``git clone`` of
    the tree URL itself."""
    extractor = AiSbomExtractor()
    subfolder_calls: list[tuple[str, str | None, str]] = []

    def fake_clone_github_subfolder(repo_root_url, ref, subpath, dest, *, token=None, **kwargs):
        subfolder_calls.append((repo_root_url, ref, subpath))
        (dest / subpath).mkdir(parents=True, exist_ok=True)
        return "deadbeef"

    monkeypatch.setattr(
        "nuguard.sbom.extractor.github_clone.clone_github_subfolder",
        fake_clone_github_subfolder,
    )
    monkeypatch.setattr(
        extractor,
        "extract_from_path",
        lambda path, config, **kwargs: AiSbomDocument(target=kwargs["source_ref"]),
    )

    url = (
        "https://x-access-token:"
        f"{_TOKEN}@github.com/NuGuardAI/openai-cs-agents-demo/tree/main/python-backend"
    )
    result = extractor.extract_from_repo(url, "main", AiSbomConfig(), cache_dir=tmp_path)

    assert subfolder_calls == [
        ("https://github.com/NuGuardAI/openai-cs-agents-demo", "main", "python-backend")
    ]
    assert result.target == "https://github.com/NuGuardAI/openai-cs-agents-demo/tree/main/python-backend"
    assert _TOKEN not in result.model_dump_json()
    expected = (tmp_path / "repo" / "openai-cs-agents-demo" / "python-backend").resolve()
    assert result.local_cloned_path == str(expected)
    assert _TOKEN not in result.local_cloned_path


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


def _stub_extractor(monkeypatch: pytest.MonkeyPatch) -> AiSbomExtractor:
    extractor = AiSbomExtractor()
    monkeypatch.setattr(extractor, "_clone_repo", lambda *, url, ref, dest: None)
    for module in ("core", "github_clone"):
        monkeypatch.setattr(
            f"nuguard.sbom.extractor.{module}.clone_github_subfolder",
            lambda root, ref, subpath, dest, **kw: (dest / subpath).mkdir(
                parents=True, exist_ok=True
            ),
        )
    monkeypatch.setattr(
        extractor,
        "extract_from_path",
        lambda path, config, **kwargs: AiSbomDocument(target=kwargs["source_ref"]),
    )
    return extractor


def test_local_cloned_path_none_without_cache_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    extractor = _stub_extractor(monkeypatch)
    doc = extractor.extract_from_repo(_DISPLAY_URL, "main", AiSbomConfig())
    assert doc.local_cloned_path is None
    doc = extractor.extract_from_repo_subfolder(
        "https://github.com/org/repo", "main", "sub", AiSbomConfig()
    )
    assert doc.local_cloned_path is None


def test_local_cloned_path_subfolder_and_relative_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extractor = _stub_extractor(monkeypatch)
    monkeypatch.chdir(tmp_path)
    doc = extractor.extract_from_repo_subfolder(
        "https://github.com/org/repo", "main", "svc/api", AiSbomConfig(), cache_dir="cache"
    )
    assert Path(doc.local_cloned_path).is_absolute()
    assert doc.local_cloned_path == str((tmp_path / "cache" / "repo" / "repo" / "svc" / "api").resolve())


@pytest.mark.parametrize("subpath", ["../evil", "/abs/path", "a/../../b"])
def test_subfolder_rejects_unsafe_subpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subpath: str
) -> None:
    extractor = _stub_extractor(monkeypatch)
    with pytest.raises(ValueError):
        extractor.extract_from_repo_subfolder(
            "https://github.com/org/repo", "main", subpath, AiSbomConfig(), cache_dir=tmp_path
        )


def test_local_cloned_path_not_serialized_and_readable_first() -> None:
    from nuguard.sbom.serializer import AiSbomSerializer

    doc = AiSbomDocument(target=_DISPLAY_URL)
    doc.local_cloned_path = "/tmp/some/cache/repo/repo"
    path = doc.local_cloned_path  # client flow: capture, then serialize
    assert path == "/tmp/some/cache/repo/repo"
    for out in (AiSbomSerializer.to_json(doc), doc.model_dump_json(), str(doc.model_dump())):
        assert "local_cloned_path" not in out and "/tmp/some/cache" not in out
    assert AiSbomDocument.model_validate(doc.model_dump()).local_cloned_path is None
    # old serialized documents (no field) still validate
    assert AiSbomDocument.model_validate({"target": "x"}).local_cloned_path is None


def test_local_cloned_path_bare_shorthand_falls_back_to_subfolder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plain clone of ``org/repo/sub`` reports not-found → subfolder clone; the
    field must point at the subfolder actually scanned, not the repo root."""
    extractor = _stub_extractor(monkeypatch)

    def not_found(*, url: str, ref: str | None, dest: Path) -> None:
        raise RuntimeError("fatal: repository 'https://github.com/org/repo/sub/' not found")

    monkeypatch.setattr(extractor, "_clone_repo", not_found)
    doc = extractor.extract_from_repo(
        "https://github.com/org/repo/sub", "main", AiSbomConfig(), cache_dir=tmp_path
    )
    assert doc.local_cloned_path == str((tmp_path / "repo" / "repo" / "sub").resolve())


def test_local_cloned_path_bare_shorthand_plain_clone_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the shorthand URL clones fine as-is, the root (not a subfolder) is reported."""
    extractor = _stub_extractor(monkeypatch)
    doc = extractor.extract_from_repo(
        "https://github.com/org/repo/sub", "main", AiSbomConfig(), cache_dir=tmp_path
    )
    # cache dir is keyed by the repo name (not the trailing "sub" segment)
    assert doc.local_cloned_path == str((tmp_path / "repo" / "repo").resolve())


def test_local_cloned_path_non_github_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extractor = _stub_extractor(monkeypatch)
    doc = extractor.extract_from_repo(
        "https://gitlab.com/org/proj.git", None, AiSbomConfig(), cache_dir=tmp_path
    )
    assert doc.local_cloned_path == str((tmp_path / "repo" / "proj").resolve())


def test_local_cloned_path_survives_generator_but_not_output_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Generator forwards the extractor's document unchanged, and the file it writes
    never contains the local path."""
    generator = SbomGenerator()

    def fake_extract(url, ref, config, cache_dir=None, source_ref=None):  # type: ignore[no-untyped-def]
        doc = AiSbomDocument(target=source_ref or url)
        doc.local_cloned_path = str(tmp_path / "cache" / "repo" / "repo")
        return doc

    generator._extractor.extract_from_repo = fake_extract  # type: ignore[method-assign]
    out = tmp_path / "sbom.json"
    doc = generator.from_repo(_DISPLAY_URL, output=out)

    assert doc.local_cloned_path == str(tmp_path / "cache" / "repo" / "repo")
    assert "local_cloned_path" not in out.read_text(encoding="utf-8")
    assert str(tmp_path / "cache") not in out.read_text(encoding="utf-8")
