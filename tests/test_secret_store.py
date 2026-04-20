"""Tests for optional encrypted secret persistence helpers."""

from __future__ import annotations

import pytest

from nuguard.common.secret_store import EncryptedBlob, SecretCipher

cryptography = pytest.importorskip("cryptography.fernet")
from cryptography.fernet import Fernet  # noqa: E402


def _key() -> str:
    return Fernet.generate_key().decode("utf-8")


def test_encrypt_and_decrypt_roundtrip() -> None:
    cipher = SecretCipher(keys={"k1": _key()}, active_key_id="k1")
    payload = {
        "auth": {"type": "bearer", "header": "Authorization: Bearer tok"},
        "target": "https://app.example.com",
    }

    blob = cipher.encrypt_json(payload)
    out = cipher.decrypt_json(blob)

    assert blob.key_id == "k1"
    assert blob.algorithm == "fernet-v1"
    assert isinstance(blob.ciphertext, str) and blob.ciphertext
    assert out == payload


def test_rewrap_to_new_key_id() -> None:
    cipher = SecretCipher(keys={"k1": _key(), "k2": _key()}, active_key_id="k1")
    payload = {"token": "abc123"}

    blob = cipher.encrypt_json(payload)
    new_blob = cipher.rewrap(blob, new_active_key_id="k2")

    assert new_blob.key_id == "k2"
    assert cipher.decrypt_json(new_blob) == payload


def test_unknown_blob_key_id_raises() -> None:
    cipher = SecretCipher(keys={"k1": _key()}, active_key_id="k1")
    blob = EncryptedBlob(
        key_id="k2",
        algorithm="fernet-v1",
        ciphertext="invalid",
        created_at="2026-01-01T00:00:00+00:00",
    )

    with pytest.raises(Exception):
        cipher.decrypt_json(blob)
