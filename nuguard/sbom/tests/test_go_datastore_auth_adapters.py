"""Tests for the Go datastore and auth framework adapters."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import GoDatastoreAdapter, GoJWTAdapter, GoOAuth2Adapter
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _extract(adapter, source: str, file_path: str = "main.go") -> list[ComponentDetection]:
    return adapter.extract(source, file_path, parse_go(source, file_path))


# ---------------------------------------------------------------------------
# Datastores
# ---------------------------------------------------------------------------

_MONGO_SRC = """
package main

import "go.mongodb.org/mongo-driver/mongo"

func run(ctx context.Context) {
	client, err := mongo.Connect(ctx, nil)
	_ = client
	_ = err
}
"""


def test_mongo_connect_emits_datastore_node() -> None:
    detections = _extract(GoDatastoreAdapter(), _MONGO_SRC)
    assert len(detections) == 1
    node = detections[0]
    assert node.component_type == ComponentType.DATASTORE
    assert node.metadata["provider"] == "mongodb"
    assert node.metadata["datastore_type"] == "document"


_REDIS_SRC = """
package main

import "github.com/redis/go-redis/v9"

func run() {
	client := redis.NewClient(&redis.Options{})
	_ = client
}
"""


def test_redis_new_client_emits_datastore_node() -> None:
    detections = _extract(GoDatastoreAdapter(), _REDIS_SRC)
    assert len(detections) == 1
    assert detections[0].metadata["provider"] == "redis"
    assert detections[0].metadata["datastore_type"] == "kv"


_SQL_SRC = """
package main

import "database/sql"

func run() {
	db, err := sql.Open("postgres", "connstring")
	_ = db
	_ = err
}
"""


def test_sql_open_resolves_driver_to_provider() -> None:
    detections = _extract(GoDatastoreAdapter(), _SQL_SRC)
    assert len(detections) == 1
    assert detections[0].metadata["provider"] == "postgresql"
    assert detections[0].metadata["datastore_type"] == "relational"


def test_sql_open_unknown_driver_falls_back_to_raw_name() -> None:
    src = """
package main

import "database/sql"

func run() {
	db, _ := sql.Open("some-custom-driver", "dsn")
	_ = db
}
"""
    detections = _extract(GoDatastoreAdapter(), src)
    assert len(detections) == 1
    assert detections[0].metadata["provider"] == "some-custom-driver"


def test_multiple_providers_in_one_file_each_get_a_node() -> None:
    src = """
package main

import (
	"database/sql"
	"github.com/redis/go-redis/v9"
)

func run() {
	db, _ := sql.Open("mysql", "dsn")
	client := redis.NewClient(&redis.Options{})
	_ = db
	_ = client
}
"""
    detections = _extract(GoDatastoreAdapter(), src)
    providers = {d.metadata["provider"] for d in detections}
    assert providers == {"mysql", "redis"}


def test_datastore_adapter_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no datastore client here")
}
"""
    assert _extract(GoDatastoreAdapter(), src) == []


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_JWT_SRC = """
package main

import "github.com/golang-jwt/jwt/v5"

func run() {
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	_ = token
}
"""


def test_jwt_sign_call_emits_auth_node_matching_regex_canonical_name() -> None:
    detections = _extract(GoJWTAdapter(), _JWT_SRC)
    assert len(detections) == 1
    node = detections[0]
    assert node.component_type == ComponentType.AUTH
    assert node.canonical_name == "auth:jwt"
    assert node.metadata["auth_type"] == "jwt"
    assert node.evidence_kind == "ast_call"


def test_jwt_adapter_no_op_without_call_site() -> None:
    src = """
package main

import "github.com/golang-jwt/jwt/v5"

func run() {
	println("imported but unused")
}
"""
    assert _extract(GoJWTAdapter(), src) == []


_OAUTH2_STRUCT_SRC = """
package main

import "golang.org/x/oauth2"

func run() {
	conf := &oauth2.Config{
		ClientID: "abc",
	}
	_ = conf
}
"""


def test_oauth2_config_struct_emits_auth_node() -> None:
    detections = _extract(GoOAuth2Adapter(), _OAUTH2_STRUCT_SRC)
    assert len(detections) == 1
    node = detections[0]
    assert node.canonical_name == "auth:oauth2"
    assert node.metadata["auth_type"] == "oauth2"
    assert node.evidence_kind == "ast_instantiation"


_OAUTH2_CALL_SRC = """
package main

import "golang.org/x/oauth2"

func run(ctx context.Context, token *oauth2.Token) {
	client := oauth2.NewClient(ctx, nil)
	_ = client
}
"""


def test_oauth2_new_client_call_emits_auth_node() -> None:
    detections = _extract(GoOAuth2Adapter(), _OAUTH2_CALL_SRC)
    assert len(detections) == 1
    assert detections[0].canonical_name == "auth:oauth2"
    assert detections[0].evidence_kind == "ast_call"


def test_oauth2_adapter_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no oauth2 here")
}
"""
    assert _extract(GoOAuth2Adapter(), src) == []
