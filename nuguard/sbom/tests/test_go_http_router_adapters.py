"""Tests for the Go HTTP router / GraphQL framework adapters."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import (
    ChiAdapter,
    EchoAdapter,
    GinAdapter,
    GorillaMuxAdapter,
    GqlgenAdapter,
    NetHTTPAdapter,
)
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [item for item in detections if item.component_type == component_type]


def _extract(adapter, source: str, file_path: str = "main.go") -> list[ComponentDetection]:
    return adapter.extract(source, file_path, parse_go(source, file_path))


_GIN_SRC = """
package main

import "github.com/gin-gonic/gin"

func main() {
	r := gin.Default()
	r.GET("/users", listUsers)
	r.POST("/users", createUser)
}
"""


def test_gin_emits_framework_and_endpoints() -> None:
    detections = _extract(GinAdapter(), _GIN_SRC)
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)
    endpoints = _by_type(detections, ComponentType.API_ENDPOINT)

    assert len(frameworks) == 1
    assert {(e.metadata["method"], e.metadata["endpoint"]) for e in endpoints} == {
        ("GET", "/users"),
        ("POST", "/users"),
    }
    assert all(e.relationships for e in endpoints)


def test_gin_ignores_non_route_calls() -> None:
    src = """
package main

import "github.com/gin-gonic/gin"

func main() {
	r := gin.Default()
	r.Use(loggerMiddleware)
}
"""
    detections = _extract(GinAdapter(), src)
    assert _by_type(detections, ComponentType.API_ENDPOINT) == []


_ECHO_SRC = """
package main

import "github.com/labstack/echo/v4"

func main() {
	e := echo.New()
	e.GET("/health", healthCheck)
}
"""


def test_echo_emits_framework_and_endpoint() -> None:
    detections = _extract(EchoAdapter(), _ECHO_SRC)
    endpoints = _by_type(detections, ComponentType.API_ENDPOINT)
    assert len(endpoints) == 1
    assert endpoints[0].metadata["method"] == "GET"
    assert endpoints[0].metadata["endpoint"] == "/health"


_CHI_SRC = """
package main

import "github.com/go-chi/chi/v5"

func main() {
	r := chi.NewRouter()
	r.Get("/records", listRecords)
	r.Delete("/records/{id}", deleteRecord)
}
"""


def test_chi_title_case_verbs_map_to_uppercase_methods() -> None:
    detections = _extract(ChiAdapter(), _CHI_SRC)
    endpoints = _by_type(detections, ComponentType.API_ENDPOINT)
    assert {(e.metadata["method"], e.metadata["endpoint"]) for e in endpoints} == {
        ("GET", "/records"),
        ("DELETE", "/records/{id}"),
    }


_NET_HTTP_SRC = """
package main

import "net/http"

func main() {
	http.HandleFunc("/ping", pingHandler)
	http.ListenAndServe(":8080", nil)
}
"""


def test_net_http_handle_func_emits_any_method_endpoint() -> None:
    detections = _extract(NetHTTPAdapter(), _NET_HTTP_SRC)
    endpoints = _by_type(detections, ComponentType.API_ENDPOINT)
    assert len(endpoints) == 1
    assert endpoints[0].metadata["method"] == "ANY"
    assert endpoints[0].metadata["endpoint"] == "/ping"


_NET_HTTP_PATTERN_SRC = """
package main

import "net/http"

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/v1/conversations", createConversation)
	mux.Handle("GET /api/v1/tickets/{id}", getTicket)
	mux.HandleFunc("GET example.com/status", status)
	mux.HandleFunc("/", notFound)
}
"""


def test_net_http_go122_patterns_carry_method_host_and_wildcards() -> None:
    detections = _extract(NetHTTPAdapter(), _NET_HTTP_PATTERN_SRC)
    endpoints = {
        endpoint.display_name: endpoint.metadata
        for endpoint in _by_type(detections, ComponentType.API_ENDPOINT)
    }
    assert set(endpoints) == {
        "POST /api/v1/conversations",
        "GET /api/v1/tickets/{id}",
        "GET example.com/status",
        "ANY /",
    }
    assert endpoints["GET /api/v1/tickets/{id}"]["endpoint"] == "/api/v1/tickets/{id}"
    assert endpoints["GET example.com/status"]["route_host"] == "example.com"
    assert "host" not in endpoints["GET example.com/status"]
    assert endpoints["GET example.com/status"]["endpoint"] == "/status"
    assert "route_host" not in endpoints["ANY /"]


def test_net_http_host_pattern_endpoint_stays_path_through_extractor(tmp_path) -> None:
    from nuguard.sbom.extractor import AiSbomExtractor
    from nuguard.sbom.tests.conftest import GO_ONLY

    (tmp_path / "main.go").write_text(_NET_HTTP_PATTERN_SRC)
    doc = AiSbomExtractor().extract_from_path(tmp_path, GO_ONLY)
    endpoints = {
        node.name: node.metadata
        for node in doc.nodes
        if node.component_type == ComponentType.API_ENDPOINT
    }
    status = next(meta for name, meta in endpoints.items() if "example.com/status" in name)
    assert status.endpoint != "example.com"
    assert status.endpoint in (None, "/status")


_NET_HTTP_MULTI_HOST_SRC = """
package main

import "net/http"

func main() {
	mux := http.NewServeMux()
	mux.Handle("GET admin.example.com/x", requireAuth(adminHandler))
	mux.HandleFunc("GET public.example.com/x", publicHandler)
}
"""


def test_net_http_same_path_on_different_hosts_keeps_separate_auth() -> None:
    detections = _extract(NetHTTPAdapter(), _NET_HTTP_MULTI_HOST_SRC)
    endpoints = {
        endpoint.display_name: endpoint.metadata
        for endpoint in _by_type(detections, ComponentType.API_ENDPOINT)
    }
    admin = endpoints["GET admin.example.com/x"]
    public = endpoints["GET public.example.com/x"]
    assert admin["endpoint"] == public["endpoint"] == "/x"
    assert admin["auth_required"] is True
    assert admin["auth_inference"] == "handler_wrapper"
    assert public["auth_required"] is False
    assert "auth_inference" not in public


_MUX_SRC = """
package main

import "github.com/gorilla/mux"

func main() {
	r := mux.NewRouter()
	r.HandleFunc("/records/{id}", getRecord).Methods("GET")
	r.HandleFunc("/records", listRecords)
}
"""


def test_gorilla_mux_reads_chained_methods_call() -> None:
    detections = _extract(GorillaMuxAdapter(), _MUX_SRC)
    endpoints = {
        (e.metadata["method"], e.metadata["endpoint"])
        for e in _by_type(detections, ComponentType.API_ENDPOINT)
    }
    assert ("GET", "/records/{id}") in endpoints
    # No .Methods() chain -> falls back to ANY
    assert ("ANY", "/records") in endpoints


_GQLGEN_SRC = """
package graph

import "github.com/99designs/gqlgen/graphql/handler"

func NewServer() {
	handler.NewDefaultServer(nil)
}
"""


def test_gqlgen_emits_framework_node_only() -> None:
    detections = _extract(GqlgenAdapter(), _GQLGEN_SRC)
    assert len(detections) == 1
    assert detections[0].component_type == ComponentType.FRAMEWORK
    assert detections[0].metadata["api_style"] == "graphql"


def test_adapters_no_op_without_matching_import() -> None:
    src = """
package main

func main() {
	println("no web framework here")
}
"""
    for adapter in (GinAdapter(), EchoAdapter(), ChiAdapter(), NetHTTPAdapter(), GorillaMuxAdapter(), GqlgenAdapter()):
        assert _extract(adapter, src) == []
