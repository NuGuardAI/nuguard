"""Local SQLite SBOM registry.

Stores AI-SBOM documents in ``~/.nuguard/nuguard.db`` using the standard
library ``sqlite3`` module (no async driver required for this lightweight
registry).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nuguard.sbom.models import AiSbomDocument

DB_PATH = Path.home() / ".nuguard" / "nuguard.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sboms (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


class LocalDb:
    """Thin SQLite wrapper for storing and retrieving AI-SBOM documents.

    Args:
        path: Path to the SQLite database file.  Defaults to
              ``~/.nuguard/nuguard.db``.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path

    def init_schema(self) -> None:
        """Create the ``sboms`` table if it does not already exist."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()

    def save_sbom(self, doc: AiSbomDocument) -> str:
        """Persist *doc* and return its generated ``sbom_id``.

        Creates the schema on first call if it does not already exist.

        Args:
            doc: The :class:`~nuguard.models.sbom.AiSbomDocument` to persist.

        Returns:
            A UUID string identifying the stored record.
        """
        self.init_schema()
        sbom_id = str(uuid.uuid4())
        data_json = doc.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sboms (id, target, generated_at, schema_version, node_count, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sbom_id,
                    doc.target,
                    doc.generated_at.isoformat(),
                    doc.schema_version,
                    len(doc.nodes),
                    data_json,
                ),
            )
            conn.commit()
        return sbom_id

    def get_sbom(self, sbom_id: str) -> AiSbomDocument | None:
        """Retrieve the SBOM with *sbom_id*, or ``None`` if not found.

        Args:
            sbom_id: UUID string returned by :meth:`save_sbom`.

        Returns:
            Deserialized :class:`~nuguard.models.sbom.AiSbomDocument` or
            ``None``.
        """
        self.init_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT data FROM sboms WHERE id = ?", (sbom_id,)
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return AiSbomDocument.model_validate_json(row[0])

    def list_sboms(self) -> list[dict[str, Any]]:
        """Return a summary list of all stored SBOMs.

        Each entry contains: ``id``, ``target``, ``generated_at``,
        ``node_count``.

        Returns:
            List of summary dicts, ordered by ``generated_at`` descending.
        """
        self.init_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT id, target, generated_at, schema_version, node_count
                FROM sboms
                ORDER BY generated_at DESC
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "target": row[1],
                "generated_at": row[2],
                "schema_version": row[3],
                "node_count": row[4],
            }
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.path))
