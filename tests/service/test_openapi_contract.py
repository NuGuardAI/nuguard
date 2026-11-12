"""The committed OpenAPI document (nuguard/service/openapi.json) must match the app.

Regenerate after an intentional contract change with::

    uv run python -m tests.service.test_openapi_contract
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from nuguard.service.app import create_app  # noqa: E402
from nuguard.service.settings import ApiKeyConfig, ServiceSettings  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parents[2] / "nuguard" / "service" / "openapi.json"


def live_openapi() -> dict[str, Any]:
    settings = ServiceSettings(api_keys=[ApiKeyConfig(id="x", sha256="0" * 64)])
    return create_app(settings, validate=False).openapi()


def test_committed_openapi_matches_app() -> None:
    assert SNAPSHOT.exists(), f"missing {SNAPSHOT}"
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(live_openapi())), (
        "nuguard/service/openapi.json is out of date. Regenerate with: "
        "uv run python -m tests.service.test_openapi_contract"
    )


def test_every_route_is_versioned_and_documents_problems() -> None:
    spec = live_openapi()
    assert spec["info"]["version"] == "1.0"
    for path, operations in spec["paths"].items():
        assert path.startswith("/v1/")
        for method, operation in operations.items():
            if path in ("/v1/healthz", "/v1/readyz"):
                continue
            assert "401" in operation["responses"], (method, path)


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(live_openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT}")
