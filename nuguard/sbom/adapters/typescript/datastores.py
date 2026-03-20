"""Datastore Detection TypeScript Adapter for Xelo SBOM.

Parsing is performed by ``xelo.core.ts_parser`` (tree-sitter when
available, regex fallback otherwise).

Supports:
- SQL: pg, mysql2, mongodb, typeorm, prisma, sequelize, drizzle
- Vector DBs: @pinecone-database/pinecone, chromadb, qdrant, weaviate, milvus
- Object Storage: @aws-sdk/client-s3, @google-cloud/storage, @azure/storage-blob
- Key-Value: redis, ioredis
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from xelo.adapters.base import ComponentDetection
from xelo.adapters.typescript._ts_regex import TSFrameworkAdapter
from xelo.core.ts_parser import TSParseResult, parse_typescript
from xelo.normalization import canonicalize_text
from xelo.types import ComponentType


_SQL_PACKAGES = {
    "pg": "postgresql",
    "postgres": "postgresql",
    "@neondatabase/serverless": "postgresql",
    "mysql2": "mysql",
    "mysql": "mysql",
    "mongodb": "mongodb",
    "mongoose": "mongodb",
    "typeorm": "typeorm",
    "@prisma/client": "prisma",
    "prisma": "prisma",
    "sequelize": "sequelize",
    "drizzle-orm": "drizzle",
    "knex": "knex",
    "better-sqlite3": "sqlite",
}

_VECTOR_PACKAGES = {
    "@pinecone-database/pinecone": "pinecone",
    "pinecone-client": "pinecone",
    "chromadb": "chromadb",
    "@qdrant/qdrant-client": "qdrant",
    "@qdrant/js-client-rest": "qdrant",
    "@weaviate/weaviate-ts-client": "weaviate",
    "weaviate-ts-client": "weaviate",
    "@zilliz/milvus-sdk-node": "milvus",
    "faiss-node": "faiss",
    "@lancedb/lancedb": "lancedb",
}

_OBJECT_STORAGE_PACKAGES = {
    "@aws-sdk/client-s3": "aws-s3",
    "aws-sdk": "aws-s3",
    "@google-cloud/storage": "gcs",
    "@azure/storage-blob": "azure-blob",
    "minio": "minio",
}

_KV_PACKAGES = {
    "redis": "redis",
    "ioredis": "redis",
    "@redis/client": "redis",
    "@upstash/redis": "redis",
    "memcached": "memcached",
}

_ALL_PACKAGES = list(
    {**_SQL_PACKAGES, **_VECTOR_PACKAGES, **_OBJECT_STORAGE_PACKAGES, **_KV_PACKAGES}
)

# class → (provider, datastore_type)
_CLASS_MAP: dict[str, tuple[str, str]] = {
    "Pool": ("postgresql", "sql"),
    "Client": ("postgresql", "sql"),
    "MongoClient": ("mongodb", "nosql"),
    "DataSource": ("typeorm", "sql"),
    "PrismaClient": ("prisma", "sql"),
    "Sequelize": ("sequelize", "sql"),
    "Pinecone": ("pinecone", "vector"),
    "PineconeClient": ("pinecone", "vector"),
    "ChromaClient": ("chromadb", "vector"),
    "QdrantClient": ("qdrant", "vector"),
    "WeaviateClient": ("weaviate", "vector"),
    "MilvusClient": ("milvus", "vector"),
    "Redis": ("redis", "kv"),
    "Cluster": ("redis", "kv"),
}


def _parse_url(url: str) -> dict[str, Any]:
    try:
        parsed = urlparse(url.strip("'\"`"))
        scheme = parsed.scheme.split("+")[0] if parsed.scheme else ""
        db = parsed.path.strip("/").split("/")[0] if parsed.path else None
        ep = (
            f"{scheme}://{parsed.hostname}:{parsed.port}"
            if parsed.hostname and parsed.port
            else None
        )
        return {"database": db, "api_endpoint": ep, "has_ssl": "ssl" in url.lower()}
    except Exception:
        return {}


class DatastoreTSAdapter(TSFrameworkAdapter):
    """Detect datastore connections in TypeScript/JavaScript files."""

    name = "datastore_ts"
    priority = 35
    handles_imports = _ALL_PACKAGES

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result: TSParseResult = (
            parse_result
            if isinstance(parse_result, TSParseResult)
            else parse_typescript(content, file_path)
        )
        if not self._detect(result):
            return []

        source = result.source or content
        detected: list[ComponentDetection] = [self._fw_node(file_path)]

        # --- Import-level detection (lower confidence) ---
        seen_providers: set[str] = set()
        for imp in result.imports:
            mod = imp.module
            for pkg_map, ds_type in [
                (_SQL_PACKAGES, "sql"),
                (_VECTOR_PACKAGES, "vector"),
                (_OBJECT_STORAGE_PACKAGES, "object-storage"),
                (_KV_PACKAGES, "kv"),
            ]:
                for pattern, provider in pkg_map.items():
                    if pattern in mod or mod == pattern:
                        if provider not in seen_providers:
                            seen_providers.add(provider)
                            detected.append(
                                self._ds_node(
                                    file_path=file_path,
                                    name=provider,
                                    provider=provider,
                                    ds_type=ds_type,
                                    line=imp.line_number,
                                    confidence=0.70,
                                )
                            )

        # --- Instantiation-level detection (higher confidence) ---
        pg_imported = any(
            mod in {"pg", "postgres", "@neondatabase/serverless"}
            for mod in {imp.module for imp in result.imports}
        )
        for inst in result.instantiations:
            info = _CLASS_MAP.get(inst.class_name)
            if info is None:
                continue
            provider, ds_type = info

            if inst.class_name in {"Pool", "Client"} and not pg_imported:
                continue

            # resolved_arguments has any const CONNECTION_URL = "..." expanded
            args = inst.resolved_arguments or inst.arguments
            url_val = self._clean(
                args.get("url") or args.get("connectionString") or args.get("uri") or args.get("0")
            )
            url_details = _parse_url(url_val) if url_val else {}

            name = (
                url_details.get("database")
                or self._assignment_name(source, inst.line_start)
                or f"{provider}_{inst.line_start}"
            )
            extra_meta: dict[str, Any] = {
                "datastore_type": ds_type,
                "provider": provider,
                "language": "typescript",
                "api_endpoint": url_details.get("api_endpoint"),
                "has_ssl": url_details.get("has_ssl", False),
            }
            if inst.class_name in {"Pinecone", "PineconeClient"}:
                extra_meta["index_name"] = self._clean(args.get("indexName") or args.get("index"))
            elif inst.class_name == "QdrantClient":
                extra_meta["collection_name"] = self._clean(
                    args.get("collectionName") or args.get("collection")
                )

            detected.append(
                self._ds_node(
                    file_path=file_path,
                    name=name,
                    provider=provider,
                    ds_type=ds_type,
                    line=inst.line_start,
                    confidence=0.90,
                    extra_meta=extra_meta,
                )
            )

        # --- S3 / Azure Blob via function calls ---
        for call in result.function_calls:
            fn = call.function_name
            args = call.resolved_arguments or call.arguments
            if "S3Client" in fn or (fn.endswith("S3") and "create" in fn.lower()):
                bucket = self._clean(args.get("Bucket") or args.get("bucket"))
                detected.append(
                    self._ds_node(
                        file_path=file_path,
                        name=bucket or f"s3_{call.line_start}",
                        provider="aws-s3",
                        ds_type="object-storage",
                        line=call.line_start,
                        confidence=0.85,
                        extra_meta={
                            "bucket_name": bucket,
                            "region": self._clean(args.get("region")),
                        },
                    )
                )
            elif "BlobServiceClient" in fn:
                container = self._clean(args.get("containerName"))
                detected.append(
                    self._ds_node(
                        file_path=file_path,
                        name=container or f"azure_blob_{call.line_start}",
                        provider="azure-blob",
                        ds_type="object-storage",
                        line=call.line_start,
                        confidence=0.85,
                        extra_meta={"container_name": container},
                    )
                )

        return detected

    def _ds_node(
        self,
        file_path: str,
        name: str,
        provider: str,
        ds_type: str,
        line: int,
        confidence: float = 0.80,
        extra_meta: dict[str, Any] | None = None,
    ) -> ComponentDetection:
        meta: dict[str, Any] = {
            "datastore_type": ds_type,
            "provider": provider,
            "language": "typescript",
        }
        if extra_meta:
            meta.update({k: v for k, v in extra_meta.items() if v is not None})
        return ComponentDetection(
            component_type=ComponentType.DATASTORE,
            canonical_name=canonicalize_text(f"{provider}:{name}".lower()),
            display_name=name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=confidence,
            metadata=meta,
            file_path=file_path,
            line=line,
            snippet=f"{provider} datastore",
            evidence_kind="ast_instantiation" if confidence >= 0.85 else "ast_import",
        )


DATASTORE_TS_IMPORTS = _ALL_PACKAGES
