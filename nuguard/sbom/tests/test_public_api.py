from __future__ import annotations

from xelo import AiSbomDocument, AiSbomConfig, AiSbomExtractor, AiSbomSerializer


def test_xelo_public_api_exports() -> None:
    assert AiSbomDocument is not None
    assert AiSbomConfig is not None
    assert AiSbomExtractor is not None
    assert AiSbomSerializer is not None
