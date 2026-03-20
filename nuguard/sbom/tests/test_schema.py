"""Tests for schema generation, anti-drift, and serialization round-trips."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xelo.extractor import AiSbomExtractor
from xelo.models import AiSbomDocument
from xelo.serializer import AiSbomSerializer
from conftest import APPS, PY_ONLY

_SCHEMA_FILE = Path(__file__).parent.parent / "src" / "xelo" / "schemas" / "aibom.schema.json"


# ---------------------------------------------------------------------------
# Anti-drift: committed schema must match AiSbomDocument.model_json_schema()
# ---------------------------------------------------------------------------


def test_committed_schema_matches_models() -> None:
    """aibom.schema.json must stay in sync with AiSbomDocument.model_json_schema().

    If this test fails, run from the oss/Xelo directory::

        python -c "
        from xelo.models import AiSbomDocument; import json
        open('src/xelo/schemas/aibom.schema.json', 'w').write(
            json.dumps(AiSbomDocument.model_json_schema(), indent=2) + '\\n'
        )"
    """
    assert _SCHEMA_FILE.exists(), f"Schema file not found: {_SCHEMA_FILE}"
    committed = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
    live = AiSbomDocument.model_json_schema()
    assert committed == live, (
        "aibom.schema.json is out of sync with AiSbomDocument Pydantic models. "
        'Regenerate it with: python -c "from xelo.models import AiSbomDocument; '
        "import json; open('src/xelo/schemas/aibom.schema.json', 'w')"
        ".write(json.dumps(AiSbomDocument.model_json_schema(), indent=2) + '\\n')\""
    )


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------


def test_cyclonedx_empty_doc_has_required_fields() -> None:
    """Minimal document (no nodes) must still produce a valid CycloneDX envelope."""
    payload = AiSbomSerializer.to_cyclonedx(AiSbomDocument(target="sample"))
    assert payload["bomFormat"] == "CycloneDX"
    assert "metadata" in payload
    assert "components" in payload


def test_extracted_doc_validates_against_schema() -> None:
    """A document from AiSbomExtractor must round-trip through model_validate."""
    doc = AiSbomExtractor().extract_from_path(APPS / "customer_service_bot", PY_ONLY)
    data = json.loads(AiSbomSerializer.to_json(doc))
    reparsed = AiSbomDocument.model_validate(data)
    assert len(reparsed.nodes) == len(doc.nodes)
    assert len(reparsed.deps) == len(doc.deps)
    assert reparsed.summary is not None
    assert reparsed.summary.use_case


def test_schema_required_field_enforced() -> None:
    """model_validate must reject a document missing the required 'target' field."""
    with pytest.raises(Exception):  # pydantic.ValidationError
        AiSbomDocument.model_validate({"nodes": [], "edges": []})
