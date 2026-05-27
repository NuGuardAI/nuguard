"""Python datastore adapter for NuGuard SBOM.

Detects relational, key-value, and vector datastore connections from Python source:

  Relational / SQL
  - SQLAlchemy: create_engine / create_async_engine + URL scheme detection
  - sqlite3.connect(...)
  - asyncpg.connect / asyncpg.create_pool
  - psycopg2.connect
  - pymongo.MongoClient / motor.AsyncIOMotorClient

  Key-Value
  - redis.Redis / redis.StrictRedis / redis.from_url
  - aioredis.from_url / aioredis.create_redis_pool

  Vector
  - chromadb.Client / chromadb.PersistentClient / chromadb.HttpClient
  - pinecone.Pinecone / PineconeClient(...)
  - qdrant_client.QdrantClient
  - weaviate.connect_to_local / weaviate.connect_to_weaviate_cloud / WeaviateClient
  - pymilvus.MilvusClient / connections.connect

Relationship edges emitted:
  TOOL -[ACCESSES]-> DATASTORE  (when tool function and datastore are in same file)
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint

# ---------------------------------------------------------------------------
# Import triggers — the adapter activates when any of these are imported
# ---------------------------------------------------------------------------

_HANDLES = [
    # SQL / relational
    "sqlalchemy",
    "sqlmodel",
    "sqlite3",
    "asyncpg",
    "psycopg2",
    "psycopg",
    "aiomysql",
    "pymysql",
    "pymongo",
    "motor",
    # Key-value
    "redis",
    "aioredis",
    "valkey",
    # Vector
    "chromadb",
    "pinecone",
    "qdrant_client",
    "weaviate",
    "pymilvus",
    "lancedb",
]

# ---------------------------------------------------------------------------
# Class-name → (provider, datastore_type)  mappings
# ---------------------------------------------------------------------------

_INSTANTIATION_MAP: dict[str, tuple[str, str]] = {
    # SQLAlchemy
    "Engine": ("sqlalchemy", "relational"),
    "AsyncEngine": ("sqlalchemy", "relational"),
    "Session": ("sqlalchemy", "relational"),
    "AsyncSession": ("sqlalchemy", "relational"),
    # sqlite3
    "Connection": ("sqlite3", "relational"),
    # asyncpg
    "Pool": ("asyncpg", "relational"),
    # MongoDB
    "MongoClient": ("mongodb", "document"),
    "AsyncIOMotorClient": ("mongodb", "document"),
    # Redis
    "Redis": ("redis", "kv"),
    "StrictRedis": ("redis", "kv"),
    "Cluster": ("redis", "kv"),
    "ConnectionPool": ("redis", "kv"),
    # Vector
    "Client": ("chromadb", "vector"),          # chromadb.Client
    "PersistentClient": ("chromadb", "vector"),
    "HttpClient": ("chromadb", "vector"),
    "Pinecone": ("pinecone", "vector"),
    "PineconeClient": ("pinecone", "vector"),
    "QdrantClient": ("qdrant", "vector"),
    "WeaviateClient": ("weaviate", "vector"),
    "MilvusClient": ("milvus", "vector"),
}

# Function-call patterns: function_name → (provider, datastore_type)
_CALL_MAP: dict[str, tuple[str, str]] = {
    # SQLAlchemy
    "create_engine": ("sqlalchemy", "relational"),
    "create_async_engine": ("sqlalchemy", "relational"),
    "sessionmaker": ("sqlalchemy", "relational"),
    "async_sessionmaker": ("sqlalchemy", "relational"),
    # sqlite3
    "connect": ("sqlite3", "relational"),       # sqlite3.connect
    # asyncpg
    "create_pool": ("asyncpg", "relational"),   # asyncpg.create_pool
    # redis
    "from_url": ("redis", "kv"),                # redis.from_url / aioredis.from_url
    "create_redis_pool": ("redis", "kv"),       # aioredis.create_redis_pool
    # weaviate
    "connect_to_local": ("weaviate", "vector"),
    "connect_to_weaviate_cloud": ("weaviate", "vector"),
    "connect_to_embedded": ("weaviate", "vector"),
}

# Module prefix → provider (used to disambiguate generic names like "connect" or "Client")
_MODULE_PROVIDER: dict[str, str] = {
    "sqlite3": "sqlite",
    "asyncpg": "postgresql",
    "psycopg2": "postgresql",
    "psycopg": "postgresql",
    "aiomysql": "mysql",
    "pymysql": "mysql",
    "pymongo": "mongodb",
    "motor": "mongodb",
    "redis": "redis",
    "aioredis": "redis",
    "valkey": "redis",
    "chromadb": "chromadb",
    "pinecone": "pinecone",
    "qdrant_client": "qdrant",
    "weaviate": "weaviate",
    "pymilvus": "milvus",
    "lancedb": "lancedb",
    "sqlalchemy": "sqlalchemy",
    "sqlmodel": "sqlalchemy",
}

# SQLAlchemy URL scheme → (provider, datastore_type)
_SQLALCHEMY_SCHEMES: dict[str, tuple[str, str]] = {
    "sqlite": ("sqlite", "relational"),
    "postgresql": ("postgresql", "relational"),
    "postgres": ("postgresql", "relational"),
    "mysql": ("mysql", "relational"),
    "mariadb": ("mysql", "relational"),
    "mssql": ("mssql", "relational"),
    "oracle": ("oracle", "relational"),
    "redshift": ("postgresql", "relational"),
}

# DatastoreType enum values
_DS_TYPE_MAP = {
    "relational": "relational",
    "kv": "kv",
    "vector": "vector",
    "document": "relational",  # treat document DBs as relational for now
    "object-storage": "relational",
}


def _parse_url(url: str) -> tuple[str | None, str | None]:
    """Return (scheme, database_name) from a connection URL."""
    try:
        parsed = urlparse(url.strip("'\"`"))
        scheme = parsed.scheme.split("+")[0].lower() if parsed.scheme else None
        db = parsed.path.strip("/").split("/")[0] or None if parsed.path else None
        return scheme, db
    except Exception:
        return None, None


def _provider_from_modules(imported_modules: set[str]) -> str | None:
    """Best-guess provider from the set of imported module names."""
    for mod in imported_modules:
        base = mod.split(".")[0]
        if base in _MODULE_PROVIDER:
            return _MODULE_PROVIDER[base]
    return None


class PythonDatastoreAdapter(FrameworkAdapter):
    """Detect datastore connections in Python source files."""

    name = "python_datastores"
    priority = 38  # between LLMClientsAdapter(35) and FastAPI(40)
    handles_imports = _HANDLES

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        imported_modules: set[str] = {imp.module or "" for imp in parse_result.imports}
        # Collect which base packages are imported
        imported_bases: set[str] = {m.split(".")[0] for m in imported_modules if m}

        detected: list[ComponentDetection] = []
        seen_providers: set[str] = set()

        # ------------------------------------------------------------------
        # Collect tool canonical names defined in this file (for ACCESSES hints)
        # ------------------------------------------------------------------
        tool_canonicals: list[str] = []
        for call in parse_result.function_calls:
            if call.function_name == "tool" and call.assigned_to:
                tool_canonicals.append(
                    canonicalize_text(f"langchain:tool:{call.assigned_to}")
                )

        def _add_ds(
            provider: str,
            ds_type: str,
            name: str,
            confidence: float,
            line: int,
            snippet: str,
            evidence_kind: str = "ast_instantiation",
            url_meta: dict[str, Any] | None = None,
        ) -> None:
            if provider in seen_providers:
                return
            seen_providers.add(provider)

            # Normalise datastore_type to known enum values
            dt = _DS_TYPE_MAP.get(ds_type, "relational")

            canon = canonicalize_text(f"datastore:{provider}")
            rels: list[RelationshipHint] = []
            for tc in tool_canonicals:
                rels.append(
                    RelationshipHint(
                        source_canonical=tc,
                        source_type=ComponentType.TOOL,
                        target_canonical=canon,
                        target_type=ComponentType.DATASTORE,
                        relationship_type="ACCESSES",
                    )
                )

            meta: dict[str, Any] = {
                "datastore_type": dt,
                "provider": provider,
                "framework": "python_datastores",
            }
            if url_meta:
                meta.update(url_meta)

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=canon,
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=confidence,
                    metadata=meta,
                    file_path=file_path,
                    line=line,
                    snippet=snippet,
                    evidence_kind=evidence_kind,
                    relationships=rels,
                )
            )

        # ------------------------------------------------------------------
        # Instantiation-level detection
        # ------------------------------------------------------------------
        for inst in parse_result.instantiations:
            mapping = _INSTANTIATION_MAP.get(inst.class_name)
            if not mapping:
                continue
            raw_provider, ds_type = mapping

            # Disambiguate generic names (Client, Connection, Pool, connect)
            # by checking which datastore package is imported.
            if raw_provider in ("chromadb", "sqlite3", "asyncpg", "redis"):
                # These are unique enough
                provider = raw_provider
            else:
                # Resolve using imported modules
                resolved = _provider_from_modules(imported_modules)
                provider = resolved or raw_provider

            # For SQLAlchemy Engine/Session without URL → just note sqlalchemy
            confidence = 0.88

            _add_ds(
                provider=provider,
                ds_type=ds_type,
                name=inst.class_name,
                confidence=confidence,
                line=inst.line,
                snippet=f"{inst.class_name}(...)",
                evidence_kind="ast_instantiation",
            )

        # ------------------------------------------------------------------
        # Function-call-level detection (create_engine, from_url, connect, etc.)
        # ------------------------------------------------------------------
        for call in parse_result.function_calls:
            mapping = _CALL_MAP.get(call.function_name)
            if not mapping:
                continue
            raw_provider, ds_type = mapping

            # Disambiguate "connect" and "from_url" by receiver module
            receiver = (call.receiver or "").split(".")[0]
            if receiver in _MODULE_PROVIDER:
                provider = _MODULE_PROVIDER[receiver]
            elif raw_provider == "sqlite3" and "sqlite3" not in imported_bases:
                # "connect" without sqlite3 import — likely something else
                continue
            else:
                provider = raw_provider

            # For create_engine / create_async_engine: inspect the URL arg
            url_meta: dict[str, Any] = {}
            url_arg: str | None = None
            if call.function_name in ("create_engine", "create_async_engine"):
                if call.positional_args:
                    first = call.positional_args[0]
                    if isinstance(first, str) and not first.startswith("$"):
                        url_arg = first
                elif "url" in call.args:
                    v = call.args["url"]
                    if isinstance(v, str) and not v.startswith("$"):
                        url_arg = v

                if url_arg:
                    scheme, db = _parse_url(url_arg)
                    if scheme and scheme in _SQLALCHEMY_SCHEMES:
                        provider, ds_type = _SQLALCHEMY_SCHEMES[scheme]
                    if db:
                        url_meta["database"] = db

            confidence = 0.92 if call.function_name in (
                "create_engine", "create_async_engine"
            ) else 0.88

            _add_ds(
                provider=provider,
                ds_type=ds_type,
                name=provider,
                confidence=confidence,
                line=call.line,
                snippet=f"{call.function_name}(...)",
                evidence_kind="ast_call",
                url_meta=url_meta or None,
            )

        # ------------------------------------------------------------------
        # Import-only detection for packages with no other signal (lower confidence)
        # ------------------------------------------------------------------
        import_only_map: dict[str, tuple[str, str]] = {
            "redis": ("redis", "kv"),
            "aioredis": ("redis", "kv"),
            "valkey": ("redis", "kv"),
            "pymongo": ("mongodb", "document"),
            "motor": ("mongodb", "document"),
            "asyncpg": ("postgresql", "relational"),
            "psycopg2": ("postgresql", "relational"),
            "psycopg": ("postgresql", "relational"),
            "aiomysql": ("mysql", "relational"),
            "pymysql": ("mysql", "relational"),
            "chromadb": ("chromadb", "vector"),
            "pinecone": ("pinecone", "vector"),
            "qdrant_client": ("qdrant", "vector"),
            "weaviate": ("weaviate", "vector"),
            "pymilvus": ("milvus", "vector"),
            "lancedb": ("lancedb", "vector"),
        }
        for base in imported_bases:
            if base not in import_only_map:
                continue
            provider, ds_type = import_only_map[base]
            _add_ds(
                provider=provider,
                ds_type=ds_type,
                name=provider,
                confidence=0.70,
                line=0,
                snippet=f"import {base}",
                evidence_kind="ast_import",
            )

        return detected
