"""Unit tests for the DatastoreTSAdapter regression fixes.

Covers:
  TestAwsSesNotMisclassifiedAsS3 — @aws-sdk/client-ses must not become aws-s3
  TestUnnamedInstanceConsolidation — repeated unnamed same-provider
    instances in a file consolidate to one node instead of fragmenting by
    line number (e.g. "Postgresql 13", "Qdrant 77" noise)
"""

from __future__ import annotations

from typing import Any

from nuguard.sbom.adapters.typescript.datastores import DatastoreTSAdapter
from nuguard.sbom.core.ts_parser import parse_typescript
from nuguard.sbom.types import ComponentType

_ADAPTER = DatastoreTSAdapter()


def _extract(code: str, file_path: str = "db.ts") -> list[Any]:
    pr = parse_typescript(code, file_path)
    return _ADAPTER.extract(code, file_path, pr)


def _ds_nodes(detections: list[Any]) -> list[Any]:
    return [d for d in detections if d.component_type == ComponentType.DATASTORE]


class TestAwsSesNotMisclassifiedAsS3:
    def test_ses_import_not_classified_as_s3(self) -> None:
        code = "import { SESClient } from '@aws-sdk/client-ses';\n"
        ds = _ds_nodes(_extract(code))
        assert not any(d.metadata.get("provider") == "aws-s3" for d in ds), (
            f"@aws-sdk/client-ses must not be classified as aws-s3, got: "
            f"{[d.metadata.get('provider') for d in ds]}"
        )

    def test_sns_import_not_classified_as_s3(self) -> None:
        code = "import { SNSClient } from '@aws-sdk/client-sns';\n"
        ds = _ds_nodes(_extract(code))
        assert not any(d.metadata.get("provider") == "aws-s3" for d in ds)

    def test_actual_s3_import_still_classified_as_s3(self) -> None:
        code = "import { S3Client } from '@aws-sdk/client-s3';\n"
        ds = _ds_nodes(_extract(code))
        assert any(d.metadata.get("provider") == "aws-s3" for d in ds), (
            f"@aws-sdk/client-s3 must still classify as aws-s3, got: "
            f"{[d.metadata.get('provider') for d in ds]}"
        )

    def test_bare_aws_sdk_v2_still_classified_as_s3(self) -> None:
        code = "import AWS from 'aws-sdk';\nconst s3 = new AWS.S3();\n"
        ds = _ds_nodes(_extract(code))
        assert any(d.metadata.get("provider") == "aws-s3" for d in ds), (
            "bare 'aws-sdk' (v2 SDK) import should still classify as aws-s3"
        )


class TestUnnamedInstanceConsolidation:
    def test_two_unnamed_postgres_pools_consolidate(self) -> None:
        # Neither instantiation is a simple `const x = new Pool()` assignment
        # (both are bare `return new Pool()`), so _assignment_name can't
        # resolve a name and both fall through to the provider-name fallback.
        code = (
            "import { Pool } from 'pg';\n"
            "export function getReadPool() {\n"
            "  return new Pool();\n"
            "}\n"
            "export function getWritePool() {\n"
            "  return new Pool();\n"
            "}\n"
        )
        ds = [d for d in _ds_nodes(_extract(code)) if d.metadata.get("provider") == "postgresql"]
        # Both instantiations have no URL and no resolvable single assignment
        # name shared between them — they must consolidate to one node keyed
        # by provider, not fragment into "postgresql_2"/"postgresql_3"-style
        # per-line noise.
        canonical_names = {d.canonical_name for d in ds}
        assert len(canonical_names) == 1, (
            f"Expected unnamed same-provider instances to consolidate, got: {canonical_names}"
        )
        assert canonical_names == {"postgresql_postgresql"}, canonical_names
