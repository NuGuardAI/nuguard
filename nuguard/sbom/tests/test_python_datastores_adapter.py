"""Unit tests for the PythonDatastoreAdapter.

Covers:
  TestCanHandle          — adapter activates on the correct import prefixes
  TestRedisDetection     — redis.Redis / StrictRedis / from_url / aioredis
  TestSQLAlchemyDetection — create_engine with URL scheme parsing
  TestSqlite3Detection   — sqlite3.connect
  TestMongoDetection     — MongoClient / AsyncIOMotorClient
  TestVectorDetection    — chromadb / pinecone / qdrant / weaviate
  TestImportOnlyDetection — lower-confidence import-only detection
  TestToolAccessesHints  — TOOL → DATASTORE ACCESSES hints for same-file @tool
  TestNegatives          — no false positives on unrelated code
"""

from __future__ import annotations

from typing import Any

import pytest

from nuguard.sbom.adapters.base import RelationshipHint
from nuguard.sbom.adapters.python.datastores import PythonDatastoreAdapter
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.types import ComponentType

_ADAPTER = PythonDatastoreAdapter()


def _extract(code: str) -> list[Any]:
    pr = parse(code)
    return _ADAPTER.extract(code, "app.py", pr)


def _by_type(detections: list[Any], ctype: ComponentType) -> list[Any]:
    return [d for d in detections if d.component_type == ctype]


def _all_hints(detections: list[Any]) -> list[RelationshipHint]:
    hints: list[RelationshipHint] = []
    for d in detections:
        hints.extend(d.relationships)
    return hints


def _ds(detections: list[Any]) -> list[Any]:
    return _by_type(detections, ComponentType.DATASTORE)


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    @pytest.mark.parametrize(
        "module",
        [
            "redis",
            "redis.client",
            "aioredis",
            "sqlalchemy",
            "sqlalchemy.ext.asyncio",
            "sqlite3",
            "asyncpg",
            "psycopg2",
            "pymongo",
            "motor",
            "chromadb",
            "pinecone",
            "qdrant_client",
            "weaviate",
            "pymilvus",
            "lancedb",
        ],
    )
    def test_activates_on_datastore_imports(self, module: str) -> None:
        assert _ADAPTER.can_handle({module}), f"Expected can_handle({module!r})==True"

    def test_does_not_activate_on_unrelated(self) -> None:
        assert not _ADAPTER.can_handle({"openai", "anthropic", "langchain", "fastapi"})

    def test_none_parse_result_returns_empty(self) -> None:
        assert _ADAPTER.extract("", "x.py", None) == []


# ---------------------------------------------------------------------------
# Redis detection
# ---------------------------------------------------------------------------


class TestRedisDetection:
    def test_redis_class_instantiation(self) -> None:
        code = "import redis\nr = redis.Redis(host='localhost', port=6379)\n"
        ds = _ds(_extract(code))
        assert ds, "Expected DATASTORE node for redis.Redis"
        assert ds[0].metadata.get("provider") == "redis"
        assert ds[0].metadata.get("datastore_type") == "kv"

    def test_redis_from_url(self) -> None:
        code = "import redis\nr = redis.from_url('redis://localhost:6379/0')\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "redis"

    def test_aioredis_from_url(self) -> None:
        code = "import aioredis\nr = aioredis.from_url('redis://localhost')\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "redis"

    def test_redis_confidence(self) -> None:
        code = "import redis\nr = redis.Redis(host='localhost', port=6379)\n"
        ds = _ds(_extract(code))
        assert ds[0].confidence >= 0.85

    def test_redis_canonical_name(self) -> None:
        code = "import redis\nr = redis.Redis()\n"
        ds = _ds(_extract(code))
        assert "redis" in ds[0].canonical_name

    def test_deduplicated_single_node_per_provider(self) -> None:
        """Multiple redis patterns in same file should yield one DATASTORE node."""
        code = (
            "import redis\n"
            "r1 = redis.Redis(host='a')\n"
            "r2 = redis.Redis(host='b')\n"
        )
        ds = _ds(_extract(code))
        providers = [d.metadata.get("provider") for d in ds]
        assert providers.count("redis") == 1


# ---------------------------------------------------------------------------
# SQLAlchemy detection
# ---------------------------------------------------------------------------


class TestSQLAlchemyDetection:
    def test_create_engine_sqlite(self) -> None:
        code = "from sqlalchemy import create_engine\nengine = create_engine('sqlite:///app.db')\n"
        ds = _ds(_extract(code))
        assert ds
        providers = {d.metadata.get("provider") for d in ds}
        assert "sqlite" in providers

    def test_create_engine_postgresql(self) -> None:
        code = (
            "from sqlalchemy import create_engine\n"
            "engine = create_engine('postgresql://user:pass@db:5432/mydb')\n"
        )
        ds = _ds(_extract(code))
        providers = {d.metadata.get("provider") for d in ds}
        assert "postgresql" in providers

    def test_create_engine_postgresql_plus_psycopg2(self) -> None:
        code = (
            "from sqlalchemy import create_engine\n"
            "engine = create_engine('postgresql+psycopg2://user@db/mydb')\n"
        )
        ds = _ds(_extract(code))
        providers = {d.metadata.get("provider") for d in ds}
        assert "postgresql" in providers

    def test_create_engine_database_name_extracted(self) -> None:
        code = (
            "from sqlalchemy import create_engine\n"
            "engine = create_engine('postgresql://localhost/inventory')\n"
        )
        ds = _ds(_extract(code))
        pg_node = next((d for d in ds if d.metadata.get("provider") == "postgresql"), None)
        assert pg_node is not None
        assert pg_node.metadata.get("database") == "inventory"

    def test_create_engine_confidence_high(self) -> None:
        code = "from sqlalchemy import create_engine\nengine = create_engine('sqlite:///x.db')\n"
        ds = _ds(_extract(code))
        assert ds[0].confidence >= 0.90

    def test_create_async_engine(self) -> None:
        code = (
            "from sqlalchemy.ext.asyncio import create_async_engine\n"
            "engine = create_async_engine('postgresql+asyncpg://localhost/mydb')\n"
        )
        ds = _ds(_extract(code))
        assert ds, "Expected DATASTORE from create_async_engine"

    def test_datastore_type_is_relational(self) -> None:
        code = "from sqlalchemy import create_engine\nengine = create_engine('sqlite:///x.db')\n"
        ds = _ds(_extract(code))
        assert ds[0].metadata.get("datastore_type") == "relational"


# ---------------------------------------------------------------------------
# sqlite3 detection
# ---------------------------------------------------------------------------


class TestSqlite3Detection:
    def test_sqlite3_connect(self) -> None:
        code = "import sqlite3\nconn = sqlite3.connect('data/app.db')\n"
        ds = _ds(_extract(code))
        assert ds, "Expected DATASTORE for sqlite3.connect"
        assert ds[0].metadata.get("provider") == "sqlite"

    def test_sqlite3_datastore_type(self) -> None:
        code = "import sqlite3\nconn = sqlite3.connect(':memory:')\n"
        ds = _ds(_extract(code))
        assert ds[0].metadata.get("datastore_type") == "relational"


# ---------------------------------------------------------------------------
# MongoDB detection
# ---------------------------------------------------------------------------


class TestMongoDetection:
    def test_pymongo_mongo_client(self) -> None:
        code = "from pymongo import MongoClient\nclient = MongoClient('mongodb://localhost')\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "mongodb"

    def test_motor_async_client(self) -> None:
        code = (
            "from motor.motor_asyncio import AsyncIOMotorClient\n"
            "client = AsyncIOMotorClient('mongodb://localhost')\n"
        )
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "mongodb"


# ---------------------------------------------------------------------------
# Vector store detection
# ---------------------------------------------------------------------------


class TestVectorDetection:
    def test_chromadb_client(self) -> None:
        code = "import chromadb\nclient = chromadb.Client()\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "chromadb"
        assert ds[0].metadata.get("datastore_type") == "vector"

    def test_chromadb_persistent_client(self) -> None:
        code = "import chromadb\nclient = chromadb.PersistentClient(path='/data')\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "chromadb"

    def test_pinecone_client(self) -> None:
        code = "from pinecone import Pinecone\npc = Pinecone(api_key='key')\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "pinecone"
        assert ds[0].metadata.get("datastore_type") == "vector"

    def test_qdrant_client(self) -> None:
        code = (
            "from qdrant_client import QdrantClient\n"
            "client = QdrantClient(url='http://localhost:6333')\n"
        )
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "qdrant"

    def test_weaviate_connect_to_local(self) -> None:
        code = "import weaviate\nclient = weaviate.connect_to_local()\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "weaviate"

    def test_vector_datastore_type(self) -> None:
        code = "import chromadb\nclient = chromadb.Client()\n"
        ds = _ds(_extract(code))
        assert ds[0].metadata.get("datastore_type") == "vector"


# ---------------------------------------------------------------------------
# Import-only detection (lower confidence)
# ---------------------------------------------------------------------------


class TestImportOnlyDetection:
    def test_import_only_yields_lower_confidence(self) -> None:
        code = "import redis\n"
        ds = _ds(_extract(code))
        # Import-only detection at 0.70 — should be below instantiation confidence
        assert ds
        assert ds[0].confidence <= 0.75

    def test_import_only_provider_correct(self) -> None:
        code = "import chromadb\n"
        ds = _ds(_extract(code))
        assert ds
        assert ds[0].metadata.get("provider") == "chromadb"

    def test_instantiation_overrides_import_detection(self) -> None:
        """When both import and instantiation are present, only one node is emitted
        (deduplication by provider) and it uses the higher-confidence evidence."""
        code = "import redis\nr = redis.Redis(host='localhost')\n"
        ds = _ds(_extract(code))
        # Only one redis node
        redis_nodes = [d for d in ds if d.metadata.get("provider") == "redis"]
        assert len(redis_nodes) == 1


# ---------------------------------------------------------------------------
# TOOL → DATASTORE ACCESSES hints (same-file)
# ---------------------------------------------------------------------------


class TestToolAccessesHints:
    def test_tool_and_datastore_in_same_file_emits_accesses_hint(self) -> None:
        code = (
            "from langchain.tools import tool\n"
            "import redis\n"
            "\n"
            "@tool\n"
            "def get_session(user_id: str) -> dict:\n"
            "    r = redis.Redis()\n"
            "    return r.get(user_id)\n"
        )
        hints = _all_hints(_extract(code))
        accesses = [h for h in hints if h.relationship_type == "ACCESSES"]
        assert accesses, "Expected TOOL -[ACCESSES]-> DATASTORE hint"
        assert accesses[0].source_type == ComponentType.TOOL
        assert accesses[0].target_type == ComponentType.DATASTORE

    def test_accesses_hint_source_canonical_matches_tool(self) -> None:
        code = (
            "from langchain.tools import tool\n"
            "import redis\n"
            "\n"
            "@tool\n"
            "def lookup_account(account_id: str): ...\n"
        )
        hints = _all_hints(_extract(code))
        accesses = [h for h in hints if h.relationship_type == "ACCESSES"]
        assert accesses
        assert "lookup_account" in accesses[0].source_canonical

    def test_multiple_tools_each_get_accesses_hint(self) -> None:
        code = (
            "from langchain.tools import tool\n"
            "import redis\n"
            "\n"
            "@tool\n"
            "def get_a(): ...\n"
            "\n"
            "@tool\n"
            "def get_b(): ...\n"
        )
        hints = _all_hints(_extract(code))
        accesses = [h for h in hints if h.relationship_type == "ACCESSES"]
        source_canonicals = {h.source_canonical for h in accesses}
        assert len(source_canonicals) >= 2

    def test_no_accesses_hints_when_no_tools(self) -> None:
        code = "import redis\nr = redis.Redis()\n"
        hints = _all_hints(_extract(code))
        accesses = [h for h in hints if h.relationship_type == "ACCESSES"]
        assert not accesses


# ---------------------------------------------------------------------------
# Negatives
# ---------------------------------------------------------------------------


class TestNegatives:
    def test_no_detections_without_datastore_imports(self) -> None:
        code = (
            "from langchain_openai import ChatOpenAI\n"
            "llm = ChatOpenAI(model='gpt-4o')\n"
        )
        pr = parse(code)
        assert not _ADAPTER.can_handle({"langchain_openai"})

    def test_multiple_providers_detected_independently(self) -> None:
        code = (
            "import redis\n"
            "import chromadb\n"
            "r = redis.Redis()\n"
            "c = chromadb.Client()\n"
        )
        ds = _ds(_extract(code))
        providers = {d.metadata.get("provider") for d in ds}
        assert "redis" in providers
        assert "chromadb" in providers

    def test_evidence_kind_instantiation(self) -> None:
        code = "import redis\nr = redis.Redis(host='localhost')\n"
        ds = _ds(_extract(code))
        # Should be ast_instantiation (not ast_import)
        assert ds[0].evidence_kind == "ast_instantiation"

    def test_evidence_kind_import_only(self) -> None:
        code = "import redis\n"
        ds = _ds(_extract(code))
        assert ds[0].evidence_kind == "ast_import"

    def test_output_types_are_component_detections(self) -> None:
        from nuguard.sbom.adapters.base import ComponentDetection

        code = "import redis\nr = redis.Redis()\n"
        for d in _extract(code):
            assert isinstance(d, ComponentDetection)
