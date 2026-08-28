"""Tests for C# datastore adapter."""

from __future__ import annotations

from typing import Any

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.csharp import CSharpDatastoreAdapter
from nuguard.sbom.core.csharp_parser import parse_csharp
from nuguard.sbom.types import ComponentType


def _extract(
    adapter: Any,
    source: str,
    path: str = "Data.cs",
) -> list[ComponentDetection]:
    return adapter.extract(source, path, parse_csharp(source, path))


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [d for d in detections if d.component_type == component_type]


# ---------------------------------------------------------------------------
# Entity Framework Core
# ---------------------------------------------------------------------------


def test_efcore_detects_dbcontext_subclass() -> None:
    source = """using Microsoft.EntityFrameworkCore;
public class AppDbContext : DbContext
{
    public DbSet<User> Users { get; set; }
}
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    assert len(ds) >= 1
    assert ds[0].metadata["provider"] == "efcore"
    assert ds[0].metadata["datastore_type"] == "relational"


def test_efcore_detects_dbcontext_instantiation() -> None:
    source = """using Microsoft.EntityFrameworkCore;
var ctx = new AppDbContext(options);
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    providers = {d.metadata["provider"] for d in ds}
    assert "efcore" in providers


# ---------------------------------------------------------------------------
# Npgsql (PostgreSQL)
# ---------------------------------------------------------------------------


def test_npgsql_detects_connection_instantiation() -> None:
    source = """using Npgsql;
var conn = new NpgsqlConnection("Host=localhost;Database=test");
conn.Open();
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    assert len(ds) >= 1
    assert ds[0].metadata["provider"] == "npgsql"
    assert ds[0].metadata["datastore_type"] == "relational"


def test_npgsql_detects_datasource() -> None:
    source = """using Npgsql;
var ds = NpgsqlDataSource.Create("Host=localhost;Database=test");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    providers = {d.metadata["provider"] for d in ds}
    assert "npgsql" in providers


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------


def test_mongodb_detects_client_instantiation() -> None:
    source = """using MongoDB.Driver;
var client = new MongoClient("mongodb://localhost:27017");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    assert len(ds) >= 1
    assert ds[0].metadata["provider"] == "mongodb"
    assert ds[0].metadata["datastore_type"] == "document"


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


def test_redis_detects_connection_multiplexer() -> None:
    source = """using StackExchange.Redis;
var conn = ConnectionMultiplexer.Connect("localhost:6379");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    assert len(ds) >= 1
    assert ds[0].metadata["provider"] == "redis"
    assert ds[0].metadata["datastore_type"] == "kv"


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def test_sqlite_detects_connection() -> None:
    source = """using Microsoft.Data.Sqlite;
var conn = new SqliteConnection("Data Source=app.db");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    assert len(ds) >= 1
    assert ds[0].metadata["provider"] == "sqlite"
    assert ds[0].metadata["datastore_type"] == "relational"


# ---------------------------------------------------------------------------
# SQL Server
# ---------------------------------------------------------------------------


def test_mssql_detects_connection() -> None:
    source = """using Microsoft.Data.SqlClient;
var conn = new SqlConnection("Server=localhost;Database=master");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    assert len(ds) >= 1
    assert ds[0].metadata["provider"] == "mssql"
    assert ds[0].metadata["datastore_type"] == "relational"


# ---------------------------------------------------------------------------
# Import-only fallback (lower confidence)
# ---------------------------------------------------------------------------


def test_import_only_produces_lower_confidence_node() -> None:
    source = """using StackExchange.Redis;
var value = cache.Get("key");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    assert len(ds) == 1
    assert ds[0].metadata["provider"] == "redis"
    assert ds[0].confidence < 0.85


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_multiple_calls_same_provider_deduplicated() -> None:
    source = """using StackExchange.Redis;
var a = ConnectionMultiplexer.Connect("localhost");
var b = ConnectionMultiplexer.Connect("localhost");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    providers = [d.metadata["provider"] for d in ds]
    assert providers.count("redis") == 1


# ---------------------------------------------------------------------------
# No false positives
# ---------------------------------------------------------------------------


def test_unrelated_csharp_ignored() -> None:
    source = """using System;
public class Greeter
{
    public string Hello(string name) => $"Hello {name}";
}
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    assert _by_type(detections, ComponentType.DATASTORE) == []


def test_string_marker_not_detected() -> None:
    source = 'var marker = "NpgsqlConnection";'
    detections = _extract(CSharpDatastoreAdapter(), source)
    assert _by_type(detections, ComponentType.DATASTORE) == []


# ---------------------------------------------------------------------------
# Multiple providers in one file
# ---------------------------------------------------------------------------


def test_multiple_providers_detected_in_one_file() -> None:
    source = """using MongoDB.Driver;
using StackExchange.Redis;
var mongo = new MongoClient("mongodb://localhost");
var redis = ConnectionMultiplexer.Connect("localhost");
"""
    detections = _extract(CSharpDatastoreAdapter(), source)
    ds = _by_type(detections, ComponentType.DATASTORE)
    providers = {d.metadata["provider"] for d in ds}
    assert "mongodb" in providers
    assert "redis" in providers
