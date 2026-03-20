"""Thin wrapper around AiSbomExtractor for the nuguard CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AiSbomConfig
from .extractor import AiSbomExtractor
from .models import AiSbomDocument


class SbomGenerator:
    """Generates an AI-SBOM by scanning source directories."""

    def __init__(self, config: AiSbomConfig | None = None) -> None:
        self.config = config or AiSbomConfig()
        self._extractor = AiSbomExtractor()

    def from_path(self, source: Path, output: Path | None = None) -> AiSbomDocument:
        """Scan *source* and return an AiSbomDocument."""
        doc = self._extractor.extract_from_path(source, self.config)
        if output is not None:
            from .serializer import AiSbomSerializer
            output.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
        return doc

    def from_repo(self, url: str, ref: str = "main", output: Path | None = None) -> AiSbomDocument:
        """Clone *url* at *ref* and return an AiSbomDocument."""
        doc = self._extractor.extract_from_repo(url, ref, self.config)
        if output is not None:
            from .serializer import AiSbomSerializer
            output.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
        return doc
