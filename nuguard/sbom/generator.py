"""Thin wrapper around AiSbomExtractor for the nuguard CLI."""

from __future__ import annotations

from pathlib import Path

from nuguard.common.url_sanitization import sanitize_repository_url

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

    def from_repo(
        self, url: str, ref: str | None = None, output: Path | None = None
    ) -> AiSbomDocument:
        """Clone *url* at *ref* and return an AiSbomDocument.

        ``ref=None`` (the default) clones the repository's default branch,
        matching ``AiSbomExtractor.extract_from_repo``'s semantics. If *url*
        targets a GitHub subfolder (``.../tree/<ref>/<subpath>`` or the bare
        shorthand ``.../org/repo/<subpath>``), the subfolder is resolved
        automatically — see ``nuguard.common.github_url``.
        """
        from nuguard.common.github_url import try_parse_github_subfolder
        from nuguard.sbom.extractor import is_repository_not_found_error

        source_ref = sanitize_repository_url(url)
        gh = try_parse_github_subfolder(url)

        if gh is None:
            doc = self._extractor.extract_from_repo(
                url, ref, self.config, source_ref=source_ref
            )
        elif not gh.is_ambiguous_shorthand:
            assert gh.subpath is not None  # guaranteed by try_parse_github_subfolder
            effective_ref = ref if ref is not None else gh.url_ref
            doc = self._extractor.extract_from_repo_subfolder(
                gh.repo_root_url,
                ref=effective_ref,
                subpath=gh.subpath,
                config=self.config,
                source_ref=source_ref,
            )
        else:
            assert gh.subpath is not None  # guaranteed by try_parse_github_subfolder
            # Bare shorthand is ambiguous — try the direct clone first, and
            # only reinterpret the trailing path as a subfolder on a
            # definitive "not found" failure (see github_url.py docstring).
            try:
                doc = self._extractor.extract_from_repo(
                    url, ref, self.config, source_ref=source_ref
                )
            except RuntimeError as exc:
                if not is_repository_not_found_error(exc):
                    raise
                doc = self._extractor.extract_from_repo_subfolder(
                    gh.repo_root_url,
                    ref=ref,
                    subpath=gh.subpath,
                    config=self.config,
                    source_ref=source_ref,
                )
        if output is not None:
            from .serializer import AiSbomSerializer
            output.write_text(AiSbomSerializer.to_json(doc), encoding="utf-8")
        return doc
