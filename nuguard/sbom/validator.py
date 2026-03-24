"""Validate an AI-SBOM document against the bundled JSON schema."""

from __future__ import annotations

from typing import Any

import jsonschema

from nuguard.common.errors import ValidationError

from .schema import get_schema


def validate_sbom(data: dict[str, Any]) -> None:
    """Validate *data* against the bundled aibom schema.

    Raises :class:`nuguard.common.errors.ValidationError` on failure.
    """
    schema = get_schema()
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(str(exc.message)) from exc
