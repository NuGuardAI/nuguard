"""Regression tests for cross-file TOOL -[ACCESSES]-> DATASTORE hint resolution.

Covers the gap where a datastore client lives in a dedicated module
(e.g. db/client.py) and is only *imported* by the tool/service file that uses
it — a file shape any reasonably-modularized app has, but which previously
produced zero ACCESSES edges since PythonDatastoreAdapter only emits a hint
when the tool and the datastore are instantiated in the same file.
"""
from __future__ import annotations

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.types import ComponentType


def _extract(tmp_path):
    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
    return AiSbomExtractor().extract_from_path(tmp_path, config)


class TestCrossFileDatastoreAccess:
    def test_tool_importing_datastore_client_from_another_module_gets_accesses_edge(
        self, tmp_path
    ) -> None:
        source_dir = tmp_path / "sample-app"
        (source_dir / "db").mkdir(parents=True)
        (source_dir / "tools").mkdir(parents=True)

        (source_dir / "db" / "client.py").write_text(
            "from sqlalchemy import create_engine\n\n"
            "engine = create_engine('postgresql://localhost/app')\n",
            encoding="utf-8",
        )
        (source_dir / "tools" / "lookup.py").write_text(
            "from langchain_core.tools import tool\n"
            "from db.client import engine\n\n"
            "@tool\n"
            "def lookup_customer(customer_id: str) -> str:\n"
            "    with engine.connect() as conn:\n"
            "        return str(conn.execute('SELECT 1'))\n",
            encoding="utf-8",
        )

        doc = _extract(source_dir)

        tool_nodes = [n for n in doc.nodes if n.component_type == ComponentType.TOOL]
        ds_nodes = [n for n in doc.nodes if n.component_type == ComponentType.DATASTORE]
        assert tool_nodes, "expected the @tool-decorated function to be detected"
        assert ds_nodes, "expected the datastore client to be detected"

        accesses_edges = [e for e in doc.edges if e.relationship_type.value == "ACCESSES"]
        tool_ids = {n.id for n in tool_nodes}
        ds_ids = {n.id for n in ds_nodes}
        assert any(
            e.source in tool_ids and e.target in ds_ids for e in accesses_edges
        ), "expected a cross-file TOOL -> DATASTORE ACCESSES edge"

    def test_same_file_case_still_works_unaffected(self, tmp_path) -> None:
        """Regression guard: the pre-existing same-file hint (tool and
        datastore instantiation in one file) must not be broken or duplicated
        by the new cross-file resolution."""
        source_dir = tmp_path / "sample-app"
        source_dir.mkdir(parents=True)

        (source_dir / "tools.py").write_text(
            "from langchain_core.tools import tool\n"
            "from sqlalchemy import create_engine\n\n"
            "engine = create_engine('postgresql://localhost/app')\n\n"
            "@tool\n"
            "def lookup_customer(customer_id: str) -> str:\n"
            "    with engine.connect() as conn:\n"
            "        return str(conn.execute('SELECT 1'))\n",
            encoding="utf-8",
        )

        doc = _extract(source_dir)
        tool_ids = {n.id for n in doc.nodes if n.component_type == ComponentType.TOOL}
        ds_ids = {n.id for n in doc.nodes if n.component_type == ComponentType.DATASTORE}
        tool_to_ds_edges = [
            e
            for e in doc.edges
            if e.relationship_type.value == "ACCESSES"
            and e.source in tool_ids
            and e.target in ds_ids
        ]
        # Exactly one direct TOOL -> DATASTORE edge — not duplicated by the new
        # cross-file resolution path running on top of the same-file hint.
        assert len(tool_to_ds_edges) == 1

    def test_tool_file_with_no_datastore_import_gets_no_edge(self, tmp_path) -> None:
        source_dir = tmp_path / "sample-app"
        source_dir.mkdir(parents=True)

        (source_dir / "tools.py").write_text(
            "from langchain_core.tools import tool\n\n"
            "@tool\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n",
            encoding="utf-8",
        )

        doc = _extract(source_dir)
        accesses_edges = [e for e in doc.edges if e.relationship_type.value == "ACCESSES"]
        assert accesses_edges == []
