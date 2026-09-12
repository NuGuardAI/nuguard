"""Tests for release registry provenance verification."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import types
import urllib.error
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_module() -> types.ModuleType:
    path = _ROOT / "scripts" / "verify_registry_artifacts.py"
    spec = importlib.util.spec_from_file_location("verify_registry_artifacts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _response(payload: object) -> _Response:
    return _Response(json.dumps(payload).encode("utf-8"))


def _npm_tarball(path: Path, name: str, version: str) -> Path:
    package_json = json.dumps({"name": name, "version": version}).encode("utf-8")
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("package/package.json")
        member.size = len(package_json)
        archive.addfile(member, io.BytesIO(package_json))
    return path


def test_verify_pypi_accepts_only_exact_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    wheel = tmp_path / "nuguard-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    digest = hashlib.sha256(b"wheel").hexdigest()
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _response(
            {"urls": [{"filename": wheel.name, "digests": {"sha256": digest}}]}
        ),
    )

    assert module.verify_pypi("nuguard", "1.2.3", tmp_path) is True

    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _response(
            {"urls": [{"filename": wheel.name, "digests": {"sha256": "0" * 64}}]}
        ),
    )
    with pytest.raises(module.VerificationError, match="do not match"):
        module.verify_pypi("nuguard", "1.2.3", tmp_path)


def test_verify_pypi_distinguishes_missing_version_from_registry_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    (tmp_path / "artifact.whl").write_bytes(b"wheel")

    def raise_http_404(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError("url", 404, "missing", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", raise_http_404)
    assert module.verify_pypi("nuguard", "1.2.3", tmp_path) is False

    def raise_http_503(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError("url", 503, "unavailable", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", raise_http_503)
    with pytest.raises(module.VerificationError, match="HTTP 503"):
        module.verify_pypi("nuguard", "1.2.3", tmp_path)


def test_verify_npm_compares_registry_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    tarball = _npm_tarball(
        tmp_path / "nuguardai-nuguard-1.2.3.tgz",
        "@nuguardai/nuguard",
        "1.2.3",
    )
    local_integrity = "sha512-" + module.base64.b64encode(
        module.hashlib.sha512(tarball.read_bytes()).digest()
    ).decode("ascii")
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _response({"dist": {"integrity": local_integrity}}),
    )
    assert module.verify_npm("@nuguardai/nuguard", "1.2.3", tarball) is True

    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _response({"dist": {"integrity": "sha512-b3RoZXI="}}),
    )
    with pytest.raises(module.VerificationError, match="does not match"):
        module.verify_npm("@nuguardai/nuguard", "1.2.3", tarball)