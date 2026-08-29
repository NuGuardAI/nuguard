"""C# datastore adapter — detects relational, key-value, and document database connections.

Covers:
- Entity Framework Core (DbContext, SqlServer/PostgreSQL/SQLite providers)
- Npgsql (PostgreSQL)
- MongoDB.Driver
- StackExchange.Redis
- Microsoft.Data.Sqlite
- Microsoft.Data.SqlClient (SQL Server)

Detection is import-driven: the adapter activates when a recognized namespace is
imported, then looks for concrete client/context instantiations to produce
high-confidence DATASTORE nodes.
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection
from ._csharp_base import CSharpFrameworkAdapter
from ._source import find_calls, mask_non_code, string_constants

# ---------------------------------------------------------------------------
# Namespace → (provider, datastore_type, display_name)
# ---------------------------------------------------------------------------

_NAMESPACE_MAP: dict[str, tuple[str, str, str]] = {
    # Entity Framework Core
    "Microsoft.EntityFrameworkCore": ("efcore", "relational", "Entity Framework Core"),
    # Npgsql (PostgreSQL)
    "Npgsql": ("npgsql", "relational", "Npgsql (PostgreSQL)"),
    # MongoDB
    "MongoDB.Driver": ("mongodb", "document", "MongoDB.Driver"),
    # Redis
    "StackExchange.Redis": ("redis", "kv", "StackExchange.Redis"),
    # SQLite
    "Microsoft.Data.Sqlite": ("sqlite", "relational", "Microsoft.Data.Sqlite"),
    # SQL Server
    "Microsoft.Data.SqlClient": ("mssql", "relational", "Microsoft.Data.SqlClient"),
}

# ---------------------------------------------------------------------------
# Class-name → (provider, datastore_type)
# Used for instantiation-level detection when the namespace is already known.
# ---------------------------------------------------------------------------

_CLASS_MAP: dict[str, tuple[str, str]] = {
    # EF Core
    "DbContext": ("efcore", "relational"),
    # Npgsql
    "NpgsqlConnection": ("npgsql", "relational"),
    "NpgsqlDataSource": ("npgsql", "relational"),
    # MongoDB
    "MongoClient": ("mongodb", "document"),
    "IMongoCollection": ("mongodb", "document"),
    # Redis
    "ConnectionMultiplexer": ("redis", "kv"),
    "ConnectionPool": ("redis", "kv"),
    # SQLite
    "SqliteConnection": ("sqlite", "relational"),
    # SQL Server
    "SqlConnection": ("mssql", "relational"),
    "SqlDataSource": ("mssql", "relational"),
}

# Fields/properties that indicate a datastore client is stored (lower confidence)
_CLIENT_FIELD_RE = re.compile(
    r"(?:private|protected|internal|public)\s+"
    r"(?:readonly\s+)?"
    r"(?P<type>NpgsqlConnection|NpgsqlDataSource|MongoClient|"
    r"ConnectionMultiplexer|IDatabase|SqliteConnection|SqlConnection|"
    r"DbContext|I DataContext)\s+"
    r"(?P<name>\w+)",
)

# EF Core DbContext subclass pattern: `class Foo : DbContext`
DbContext_SUBCLASS_RE = re.compile(
    r"class\s+\w+\s*:\s*(?:\w+\s*,\s*)*DbContext\b"
)

# MongoDB connection string pattern (for URL metadata)
_MONGO_URL_RE = re.compile(
    r"""(?:mongodb(?:\+srv)?://[^\s"']+|(?:"mongodb(?:\+srv)?://[^"]+")|(?:'mongodb(?:\+srv)?://[^']+'))""",
    re.IGNORECASE,
)

# Redis connection string pattern
_REDIS_URL_RE = re.compile(
    r"""(?:(?:redis|rediss)://[^\s"']+|(?:"(?:redis|rediss)://[^"]+")|(?:'(?:redis|rediss)://[^']+'))""",
    re.IGNORECASE,
)


class CSharpDatastoreAdapter(CSharpFrameworkAdapter):
    """Detect datastore connections in C# source files."""

    name = "csharp_datastores"
    priority = 38  # Match Python datastore adapter priority
    handles_namespaces = list(_NAMESPACE_MAP)

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(content, file_path, parse_result)
        code = mask_non_code(content)

        # Collect which providers are imported — sort by length descending so
        # more-specific namespaces match before their parents.
        imported_providers: set[str] = set()
        provider_lines: dict[str, int] = {}
        sorted_ns = sorted(_NAMESPACE_MAP, key=len, reverse=True)
        for directive in result.using_directives:
            ns = directive.namespace.removeprefix("global::")
            for prefix in sorted_ns:
                provider, _, _ = _NAMESPACE_MAP[prefix]
                if ns == prefix or ns.startswith(prefix + "."):
                    imported_providers.add(provider)
                    provider_lines.setdefault(provider, directive.line)
                    break

        if not imported_providers:
            return []

        constants = string_constants(result)
        detections: list[ComponentDetection] = []
        seen_providers: set[str] = set()

        # ---- DbContext subclass detection (high confidence) ----
        if "efcore" in imported_providers:
            for match in DbContext_SUBCLASS_RE.finditer(code):
                canonical = canonicalize_text("datastore:efcore")
                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.DATASTORE,
                        canonical_name=canonical,
                        display_name="DbContext",
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={
                            "datastore_type": "relational",
                            "provider": "efcore",
                            "framework": "csharp_datastores",
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=0,
                        snippet=match.group(0)[:80],
                        evidence_kind="ast_inheritance",
                    )
                )
                seen_providers.add("efcore")
                break

        # ---- Instantiation-level detection (high confidence) ----
        calls = find_calls(content, set(_CLASS_MAP))

        for call in calls:
            mapping = _CLASS_MAP.get(call.name)
            if not mapping:
                continue
            provider, ds_type = mapping

            # Validate: the provider's namespace must be imported
            if provider not in imported_providers:
                continue

            # Resolve the instantiated class name for display
            display_name = call.name
            canonical = canonicalize_text(f"datastore:{provider}")

            meta: dict[str, Any] = {
                "datastore_type": ds_type,
                "provider": provider,
                "framework": "csharp_datastores",
                "language": "csharp",
            }

            # Extract connection-string metadata if present
            url_meta = _extract_url_meta(provider, call, constants)
            if url_meta:
                meta.update(url_meta)

            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=canonical,
                    display_name=display_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata=meta,
                    file_path=file_path,
                    line=call.line,
                    snippet=call.snippet,
                    evidence_kind="ast_call",
                )
            )
            seen_providers.add(provider)

        # ---- Field/property declaration detection (medium confidence) ----
        for match in _CLIENT_FIELD_RE.finditer(code):
            class_name = match.group("type")
            field_name = match.group("name")
            mapping = _CLASS_MAP.get(class_name)
            if not mapping:
                continue
            provider, ds_type = mapping
            if provider not in imported_providers or provider in seen_providers:
                continue

            canonical = canonicalize_text(f"datastore:{provider}")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=canonical,
                    display_name=class_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.82,
                    metadata={
                        "datastore_type": ds_type,
                        "provider": provider,
                        "field_name": field_name,
                        "framework": "csharp_datastores",
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=0,
                    snippet=f"{class_name} {field_name}",
                    evidence_kind="ast_field",
                )
            )
            seen_providers.add(provider)

        # ---- Import-only fallback (low confidence) ----
        for provider in imported_providers:
            if provider in seen_providers:
                continue
            ds_type = next(
                (dt for _, (p, dt, _) in _NAMESPACE_MAP.items() if p == provider),
                "relational",
            )
            display = next(
                (d for _, (p, _, d) in _NAMESPACE_MAP.items() if p == provider),
                provider,
            )
            canonical = canonicalize_text(f"datastore:{provider}")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=canonical,
                    display_name=display,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.70,
                    metadata={
                        "datastore_type": ds_type,
                        "provider": provider,
                        "framework": "csharp_datastores",
                        "language": "csharp",
                    },
                    file_path=file_path,
                    line=provider_lines.get(provider, 0),
                    snippet=f"using {_provider_namespace(provider)}",
                    evidence_kind="ast_import",
                )
            )

        return _dedupe(detections)


def _provider_namespace(provider: str) -> str:
    """Return the primary namespace for a provider key."""
    for ns, (p, _, _) in _NAMESPACE_MAP.items():
        if p == provider:
            return ns
    return provider


def _extract_url_meta(
    provider: str,
    call: Any,
    constants: dict[str, str],
) -> dict[str, Any]:
    """Extract connection-string metadata from constructor arguments."""
    meta: dict[str, Any] = {}

    if not call.positional_arguments:
        return meta

    first_arg = call.positional_arguments[0]
    # Resolve constant references
    resolved = constants.get(first_arg, first_arg) if first_arg in constants else first_arg
    if not resolved or not isinstance(resolved, str):
        return meta

    # Clean up string delimiters
    cleaned = resolved.strip("'\"`")
    if cleaned.startswith("$("):
        return meta

    if provider == "mongodb":
        m = _MONGO_URL_RE.search(cleaned)
        if m:
            meta["connection_string_preview"] = cleaned[:120]
    elif provider == "redis":
        m = _REDIS_URL_RE.search(cleaned)
        if m:
            meta["connection_string_preview"] = cleaned[:120]
    elif provider in ("npgsql", "sqlite", "mssql"):
        # These typically take a connection string as the first arg
        if len(cleaned) > 5:
            meta["connection_string_preview"] = cleaned[:120]

    return meta


def _dedupe(detections: list[ComponentDetection]) -> list[ComponentDetection]:
    seen: set[tuple[ComponentType, str]] = set()
    result: list[ComponentDetection] = []
    for detection in detections:
        key = (detection.component_type, detection.canonical_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(detection)
    return result


__all__ = ["CSharpDatastoreAdapter"]
