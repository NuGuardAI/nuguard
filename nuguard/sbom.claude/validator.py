"""Validate an AI-SBOM dict against the bundled JSON Schema.

Usage::

    from nuguard.sbom.validator import validate_sbom

    validate_sbom(data)  # raises ValidationError on failure
"""

from __future__ import annotations

import importlib.resources
import json
from typing import Any

import jsonschema

from nuguard.common.errors import ValidationError

_schema_cache: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any]:
    global _schema_cache
    if _schema_cache is None:
        pkg = importlib.resources.files("nuguard.sbom.schema")
        schema_text = (pkg / "aibom.schema.json").read_text(encoding="utf-8")
        _schema_cache = json.loads(schema_text)
    return _schema_cache


def validate_sbom(data: dict[str, Any]) -> None:
    """Validate *data* against the bundled ``aibom.schema.json``.

    Args:
        data: Parsed JSON dict representing an AI-SBOM document.

    Raises:
        :class:`~nuguard.common.errors.ValidationError`: When *data* does not
            conform to the schema.  The error message includes the first
            validation failure reported by ``jsonschema``.
    """
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(data))
    if errors:
        # Report the most specific (deepest) error first.
        best = jsonschema.exceptions.best_match(errors)
        raise ValidationError(
            f"SBOM schema validation failed: {best.message} "
            f"(path: {' > '.join(str(p) for p in best.absolute_path)})"
        )
