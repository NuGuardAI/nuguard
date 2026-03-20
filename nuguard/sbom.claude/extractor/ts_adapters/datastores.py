"""TypeScript datastores adapter.

Detects vector databases, relational databases, and KV stores in
TypeScript/JavaScript source files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from nuguard.models.sbom import (
    DatastoreType,
    Edge,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
)
from nuguard.sbom.extractor.ts_parser import TSParseResult

_TRIGGER_MODULES = frozenset(
    {
        "@pinecone-database/pinecone",
        "@weaviate/weaviate-client",
        "qdrant-client",
        "@qdrant/js-client-rest",
        "chromadb",
        "@upstash/redis",
        "@supabase/supabase-js",
        "mongoose",
        "prisma",
        "@prisma/client",
        "typeorm",
        "pg",
        "mysql2",
        "redis",
    }
)

_VECTOR_CONSTRUCTORS = frozenset(
    {
        "Pinecone",
        "WeaviateClient",
        "QdrantClient",
        "ChromaClient",
        "MilvusClient",
    }
)

_RELATIONAL_CONSTRUCTORS = frozenset(
    {
        "PrismaClient",
        "DataSource",  # TypeORM
        "Pool",  # pg
        "Sequelize",
        "Knex",
    }
)

_KV_CONSTRUCTORS = frozenset(
    {
        "Redis",
        "Memcached",
    }
)


def _stable_id(name: str, node_type: NodeType) -> str:
    raw = f"{name}:{node_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _evidence(
    kind: EvidenceKind,
    confidence: float,
    detail: str,
    path: Path,
    line: int | None = None,
) -> Evidence:
    return Evidence(
        kind=kind,
        confidence=confidence,
        detail=detail,
        location=EvidenceLocation(path=str(path), line=line),
    )


class DatastoresTSAdapter:
    """Extract datastore usage from a parsed TypeScript/JavaScript file."""

    TRIGGER_MODULES = _TRIGGER_MODULES

    def can_handle(self, result: TSParseResult) -> bool:
        return bool(result.has_module & self.TRIGGER_MODULES)

    def extract(self, file_path: Path, result: TSParseResult) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        seen: set[str] = set()

        for call in result.calls:
            name = call.name.replace("new ", "")
            line = call.line

            if name in _VECTOR_CONSTRUCTORS:
                if name not in seen:
                    seen.add(name)
                    nid = _stable_id(name, NodeType.DATASTORE)
                    nodes.append(
                        Node(
                            id=nid,
                            name=name,
                            component_type=NodeType.DATASTORE,
                            confidence=0.9,
                            metadata=NodeMetadata(datastore_type=DatastoreType.VECTOR),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST_INSTANTIATION,
                                    0.9,
                                    f"TS vector store new {name}()",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )

            elif name in _RELATIONAL_CONSTRUCTORS:
                if name not in seen:
                    seen.add(name)
                    nid = _stable_id(name, NodeType.DATASTORE)
                    nodes.append(
                        Node(
                            id=nid,
                            name=name,
                            component_type=NodeType.DATASTORE,
                            confidence=0.9,
                            metadata=NodeMetadata(datastore_type=DatastoreType.RELATIONAL),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST_INSTANTIATION,
                                    0.9,
                                    f"TS relational DB new {name}()",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )

            elif name in _KV_CONSTRUCTORS:
                if name not in seen:
                    seen.add(name)
                    nid = _stable_id(name, NodeType.DATASTORE)
                    nodes.append(
                        Node(
                            id=nid,
                            name=name,
                            component_type=NodeType.DATASTORE,
                            confidence=0.85,
                            metadata=NodeMetadata(datastore_type=DatastoreType.KV),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST_INSTANTIATION,
                                    0.85,
                                    f"TS KV store new {name}()",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )

            # mongoose.connect(...) → relational/document
            elif name in ("connect",) and "mongoose" in str(result.has_module):
                if "mongoose" not in seen:
                    seen.add("mongoose")
                    nid = _stable_id("mongoose", NodeType.DATASTORE)
                    nodes.append(
                        Node(
                            id=nid,
                            name="mongoose",
                            component_type=NodeType.DATASTORE,
                            confidence=0.85,
                            metadata=NodeMetadata(datastore_type=DatastoreType.RELATIONAL),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST,
                                    0.85,
                                    "TS mongoose.connect()",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )

            # createClient → detect from module context
            elif name == "createClient":
                # Check if supabase or redis module present
                if "@supabase/supabase-js" in result.has_module:
                    ds_name = "supabase"
                    ds_type = DatastoreType.RELATIONAL
                elif "@upstash/redis" in result.has_module or "redis" in result.has_module:
                    ds_name = "redis"
                    ds_type = DatastoreType.KV
                else:
                    ds_name = "createClient"
                    ds_type = DatastoreType.KV
                if ds_name not in seen:
                    seen.add(ds_name)
                    nid = _stable_id(ds_name, NodeType.DATASTORE)
                    nodes.append(
                        Node(
                            id=nid,
                            name=ds_name,
                            component_type=NodeType.DATASTORE,
                            confidence=0.8,
                            metadata=NodeMetadata(datastore_type=ds_type),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST,
                                    0.8,
                                    f"TS createClient() → {ds_name}",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )

            # createPool → pg pool
            elif name == "createPool":
                if "pg_pool" not in seen:
                    seen.add("pg_pool")
                    nid = _stable_id("pg_pool", NodeType.DATASTORE)
                    nodes.append(
                        Node(
                            id=nid,
                            name="pg_pool",
                            component_type=NodeType.DATASTORE,
                            confidence=0.85,
                            metadata=NodeMetadata(datastore_type=DatastoreType.RELATIONAL),
                            evidence=[
                                _evidence(
                                    EvidenceKind.AST,
                                    0.85,
                                    "TS createPool() — pg connection pool",
                                    file_path,
                                    line,
                                )
                            ],
                        )
                    )

        return nodes, []
