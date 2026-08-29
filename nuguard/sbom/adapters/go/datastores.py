"""Go datastore adapter.

Detects datastore client construction from Go source:

- MongoDB: ``go.mongodb.org/mongo-driver/mongo`` — ``mongo.Connect(...)`` /
  ``mongo.NewClient(...)``
- Redis: ``github.com/redis/go-redis/v9`` / ``github.com/go-redis/redis``
  (any version) — ``redis.NewClient(...)``
- SQL: stdlib ``database/sql`` — ``sql.Open(driverName, dsn)``, provider
  resolved from the driver-name string argument

One DATASTORE node is emitted per distinct provider found in a file,
mirroring the Python datastore adapter's per-provider dedup
(``datastore:{provider}``).
"""

from __future__ import annotations

from typing import Any

from ...core.go_parser import GoParseResult, parse_go
from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._go_base import GoFrameworkAdapter

_MONGO_MODULE = "go.mongodb.org/mongo-driver/mongo"
_REDIS_MODULES = (
    "github.com/redis/go-redis",
    "github.com/go-redis/redis",
)
_SQL_MODULE = "database/sql"

_MONGO_CALLS = {"Connect", "NewClient"}
_REDIS_CALLS = {"NewClient", "NewClusterClient", "NewFailoverClient"}

_SQL_DRIVER_PROVIDERS: dict[str, str] = {
    "postgres": "postgresql",
    "pgx": "postgresql",
    "pq": "postgresql",
    "mysql": "mysql",
    "sqlite3": "sqlite",
    "sqlite": "sqlite",
    "sqlserver": "sqlserver",
    "mssql": "sqlserver",
    "clickhouse": "clickhouse",
    "snowflake": "snowflake",
    "oracle": "oracle",
    "godror": "oracle",
}


class GoDatastoreAdapter(GoFrameworkAdapter):
    """Detect MongoDB, Redis, and stdlib ``database/sql`` datastore clients."""

    name = "go_datastores"
    priority = 60
    handles_imports = [_MONGO_MODULE, *_REDIS_MODULES, _SQL_MODULE]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = (
            parse_result
            if isinstance(parse_result, GoParseResult)
            else parse_go(content, file_path)
        )
        import_paths = self._import_paths(result)
        if not self.can_handle(result):
            return []

        detections: list[ComponentDetection] = []
        seen_providers: set[str] = set()

        def _emit(
            *,
            provider: str,
            datastore_type: str,
            display_name: str,
            line: int,
            snippet: str,
        ) -> None:
            if provider in seen_providers:
                return
            seen_providers.add(provider)
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=canonicalize_text(f"datastore:{provider}"),
                    display_name=display_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "datastore_type": datastore_type,
                        "provider": provider,
                        "framework": "go_datastores",
                        "language": "golang",
                    },
                    file_path=file_path,
                    line=line,
                    snippet=snippet,
                    evidence_kind="ast_call",
                )
            )

        has_mongo = any(self._matches_module_path(p, _MONGO_MODULE) for p in import_paths)
        has_redis = any(
            self._matches_module_path(p, mod) for p in import_paths for mod in _REDIS_MODULES
        )
        has_sql = any(self._matches_module_path(p, _SQL_MODULE) for p in import_paths)

        for call in result.function_calls:
            if has_mongo and call.receiver == "mongo" and call.function_name in _MONGO_CALLS:
                _emit(
                    provider="mongodb",
                    datastore_type="document",
                    display_name="MongoDB",
                    line=call.line,
                    snippet=call.source_snippet or f"mongo.{call.function_name}(...)",
                )
            elif has_redis and call.receiver == "redis" and call.function_name in _REDIS_CALLS:
                _emit(
                    provider="redis",
                    datastore_type="kv",
                    display_name="Redis",
                    line=call.line,
                    snippet=call.source_snippet or f"redis.{call.function_name}(...)",
                )
            elif has_sql and call.receiver == "sql" and call.function_name == "Open":
                driver = self._resolve(call, 0)
                if not driver:
                    continue
                provider = _SQL_DRIVER_PROVIDERS.get(driver.lower(), driver.lower())
                _emit(
                    provider=provider,
                    datastore_type="relational",
                    display_name=provider.title(),
                    line=call.line,
                    snippet=call.source_snippet or f"sql.Open({driver!r}, ...)",
                )

        return detections


__all__ = ["GoDatastoreAdapter"]
