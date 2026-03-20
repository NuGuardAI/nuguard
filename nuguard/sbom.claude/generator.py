"""Thin facade over :class:`~nuguard.sbom.extractor.core.AiSbomExtractor`.

Usage::

    from nuguard.sbom.generator import SbomGenerator

    gen = SbomGenerator()
    doc = gen.from_path(Path("./my-agent-app"), output=Path("app.sbom.json"))
"""

from __future__ import annotations

import json
from pathlib import Path

from nuguard.common.logging import get_logger
from nuguard.models.sbom import AiSbomDocument
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.extractor.core import AiSbomExtractor
from nuguard.sbom.extractor.serializer import AiSbomSerializer

_log = get_logger(__name__)


class SbomGenerator:
    """High-level SBOM generator that wraps :class:`AiSbomExtractor`.

    Args:
        config: Extractor configuration.  Uses defaults when ``None``.
    """

    def __init__(self, config: AiSbomConfig | None = None) -> None:
        self._config = config or AiSbomConfig()
        self._extractor = AiSbomExtractor()

    def from_path(
        self,
        path: Path,
        output: Path | None = None,
    ) -> AiSbomDocument:
        """Generate an SBOM by scanning the directory at *path*.

        Args:
            path: Root directory to scan.
            output: When provided, the SBOM JSON is written to this path.

        Returns:
            Generated :class:`~nuguard.models.sbom.AiSbomDocument`.
        """
        doc = self._extractor.extract_from_path(path, config=self._config)
        if output is not None:
            self._write(doc, output)
        return doc

    def from_url(
        self,
        url: str,
        ref: str = "main",
        output: Path | None = None,
    ) -> AiSbomDocument:
        """Clone *url* at *ref*, scan it, and return the SBOM.

        Args:
            url: Git repository URL.
            ref: Branch or tag to check out.
            output: When provided, the SBOM JSON is written to this path.

        Returns:
            Generated :class:`~nuguard.models.sbom.AiSbomDocument`.
        """
        doc = self._extractor.extract_from_repo(url, ref=ref, config=self._config)
        if output is not None:
            self._write(doc, output)
        return doc

    @staticmethod
    def _write(doc: AiSbomDocument, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
        _log.info("SBOM written to %s (%d nodes)", output, len(doc.nodes))
