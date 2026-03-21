"""Canary data configuration and response scanner."""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

_log = logging.getLogger(__name__)


class CanaryRecord(BaseModel):
    resource: str
    id: str
    fields: dict[str, str] = Field(default_factory=dict)
    watch_values: list[str] = Field(default_factory=list)


class CanaryTenant(BaseModel):
    tenant_id: str
    session_token: str = ""
    records: list[CanaryRecord] = Field(default_factory=list)


class CanaryConfig(BaseModel):
    global_watch_values: list[str] = Field(default_factory=list)
    tenants: list[CanaryTenant] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "CanaryConfig":
        """Load a CanaryConfig from a JSON file."""
        data = json.loads(path.read_text())
        return cls.model_validate(data)

    def all_watch_values(self) -> list[str]:
        """Return a deduplicated list of all canary watch values."""
        values = list(self.global_watch_values)
        for tenant in self.tenants:
            for record in tenant.records:
                values.extend(record.watch_values)
        return list(set(values))


class CanaryScanner:
    """Scans a response string for canary values (verbatim or encoded)."""

    def __init__(self, config: CanaryConfig) -> None:
        self._watch = config.all_watch_values()

    def scan(self, text: str) -> list[str]:
        """Return list of matched canary values found verbatim in text."""
        return [v for v in self._watch if v and v in text]

    def scan_decoded(self, text: str) -> list[str]:
        """Return canary values found after decoding base64, hex, and unicode escapes."""
        found: list[str] = []
        # Try base64 decoding of text chunks
        try:
            decoded_b64 = base64.b64decode(text + "==", validate=False).decode(
                "utf-8", errors="ignore"
            )
            found.extend(v for v in self._watch if v and v in decoded_b64)
        except Exception:
            pass
        # Hex decode
        try:
            hex_clean = re.sub(r"[^0-9a-fA-F]", "", text)
            if len(hex_clean) >= 4 and len(hex_clean) % 2 == 0:
                decoded_hex = bytes.fromhex(hex_clean).decode("utf-8", errors="ignore")
                found.extend(v for v in self._watch if v and v in decoded_hex)
        except Exception:
            pass
        # Unicode escapes
        try:
            decoded_uni = text.encode().decode("unicode_escape", errors="ignore")
            found.extend(v for v in self._watch if v and v in decoded_uni)
        except Exception:
            pass
        return list(set(found))

    def has_match(self, text: str) -> bool:
        """Return True if any canary value is found verbatim or encoded."""
        return bool(self.scan(text) or self.scan_decoded(text))
