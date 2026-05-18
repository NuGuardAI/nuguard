"""Helpers for encrypting sensitive config blobs before persistence.

This module provides an optional encryption-at-rest pattern for future server-
side storage of run configs or tokens. It does not persist data itself.

Design goals:
- Keep plaintext handling local and short-lived.
- Include key IDs in ciphertext envelopes for safe key rotation.
- Make no assumptions about storage backend (SQLite/Postgres/object store).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nuguard.common.errors import ConfigError


def generate_secret_key() -> str:
    """Return a new Fernet key as a UTF-8 string."""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ConfigError(
            "cryptography is required for encrypted credential persistence. "
            "Install with: pip install cryptography"
        ) from exc

    return Fernet.generate_key().decode("utf-8")


@dataclass(slots=True)
class EncryptedBlob:
    """Serialized encrypted payload for persistence layers."""

    key_id: str
    algorithm: str
    ciphertext: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "key_id": self.key_id,
            "algorithm": self.algorithm,
            "ciphertext": self.ciphertext,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EncryptedBlob":
        return cls(
            key_id=str(raw["key_id"]),
            algorithm=str(raw["algorithm"]),
            ciphertext=str(raw["ciphertext"]),
            created_at=str(raw["created_at"]),
        )


class SecretCipher:
    """Key-ID aware encrypt/decrypt helper for JSON-serializable payloads."""

    ALGORITHM = "fernet-v1"

    def __init__(self, keys: dict[str, str], active_key_id: str) -> None:
        if not keys:
            raise ConfigError("SecretCipher requires at least one encryption key")
        if active_key_id not in keys:
            raise ConfigError(
                f"active_key_id {active_key_id!r} is not present in configured keys"
            )

        self._fernets: dict[str, Any] = {}
        self._active_key_id = active_key_id

        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise ConfigError(
                "cryptography is required for encrypted credential persistence. "
                "Install with: pip install cryptography"
            ) from exc

        for key_id, key in keys.items():
            try:
                self._fernets[key_id] = Fernet(key.encode("utf-8"))
            except Exception as exc:
                raise ConfigError(f"Invalid encryption key for key_id={key_id!r}") from exc

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def encrypt_json(self, payload: dict[str, Any]) -> EncryptedBlob:
        plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        token = self._fernets[self._active_key_id].encrypt(plaintext).decode("utf-8")
        return EncryptedBlob(
            key_id=self._active_key_id,
            algorithm=self.ALGORITHM,
            ciphertext=token,
            created_at=datetime.now(UTC).isoformat(),
        )

    def decrypt_json(self, blob: EncryptedBlob) -> dict[str, Any]:
        if blob.algorithm != self.ALGORITHM:
            raise ConfigError(
                f"Unsupported secret blob algorithm: {blob.algorithm!r}"
            )
        if blob.key_id not in self._fernets:
            raise ConfigError(
                f"No decryption key configured for key_id={blob.key_id!r}"
            )

        raw = self._fernets[blob.key_id].decrypt(blob.ciphertext.encode("utf-8"))
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ConfigError("Decrypted payload is not a JSON object")
        return decoded

    def rewrap(self, blob: EncryptedBlob, new_active_key_id: str | None = None) -> EncryptedBlob:
        """Decrypt with source key and re-encrypt under the active (or supplied) key."""
        payload = self.decrypt_json(blob)
        if new_active_key_id is not None:
            if new_active_key_id not in self._fernets:
                raise ConfigError(
                    f"Cannot rewrap to unknown key_id={new_active_key_id!r}"
                )
            old_active = self._active_key_id
            self._active_key_id = new_active_key_id
            try:
                return self.encrypt_json(payload)
            finally:
                self._active_key_id = old_active
        return self.encrypt_json(payload)
