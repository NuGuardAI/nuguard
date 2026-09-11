"""Tests for Go HTTP request-schema and auth metadata extraction."""

from __future__ import annotations

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.go import (
    ChiAdapter,
    EchoAdapter,
    GinAdapter,
    NetHTTPAdapter,
)
from nuguard.sbom.core.go_parser import parse_go
from nuguard.sbom.types import ComponentType


def _extract(
    adapter: (GinAdapter | EchoAdapter | ChiAdapter | NetHTTPAdapter),
    source: str,
    file_path: str = "main.go",
) -> list[ComponentDetection]:
    return adapter.extract(
        source,
        file_path,
        parse_go(source, file_path),
    )


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [detection for detection in detections if detection.component_type == component_type]


def _endpoint(
    detections: list[ComponentDetection],
) -> ComponentDetection:
    endpoints = _by_type(
        detections,
        ComponentType.API_ENDPOINT,
    )

    assert len(endpoints) == 1

    return endpoints[0]


def test_gin_infers_schema_chat_key_and_route_basic_auth() -> None:
    source = """package main

import web "github.com/gin-gonic/gin"

type ChatRequest struct {
    Message string        `json:"message"`
    History []ChatMessage `json:"history,omitempty"`
    Secret  string        `json:"-"`
}

func chatHandler(c *web.Context) {
    var request ChatRequest
    _ = c.ShouldBindJSON(&request)
}

func main() {
    router := web.New()
    router.POST(
        "/chat",
        web.BasicAuth(web.Accounts{"admin": "do-not-copy"}),
        chatHandler,
    )
}
"""

    detections = _extract(
        GinAdapter(),
        source,
    )
    endpoint = _endpoint(detections)
    auth = _by_type(
        detections,
        ComponentType.AUTH,
    )

    assert endpoint.metadata["request_body_schema"] == {
        "message": "string",
        "history": "[]ChatMessage",
    }
    assert endpoint.metadata["chat_payload_key"] == "message"
    assert endpoint.metadata["chat_payload_list"] is False
    assert endpoint.metadata["accepts_user_input"] is True
    assert endpoint.metadata["auth_required"] is True
    assert endpoint.metadata["auth_type"] == "basic"

    assert len(auth) == 1
    assert auth[0].metadata["auth_type"] == "basic"
    assert auth[0].metadata["auth_detail"] == {
        "protocols": ["basic"],
        "enforcement_strict": True,
    }
    assert auth[0].snippet == "web.BasicAuth(...)"
    assert "do-not-copy" not in auth[0].snippet
    assert len(auth[0].relationships) == 1
    assert auth[0].relationships[0].relationship_type == "PROTECTS"
    assert auth[0].relationships[0].target_canonical == endpoint.canonical_name


def test_gin_group_middleware_is_applied_to_group_route() -> None:
    source = """package main

import "github.com/gin-gonic/gin"

type ChatRequest struct {
    Prompt string `json:"prompt"`
}

func chatHandler(c *gin.Context) {
    request := ChatRequest{}
    _ = c.BindJSON(&request)
}

func main() {
    router := gin.New()
    protected := router.Group(
        "/api",
        gin.BasicAuth(gin.Accounts{"admin": "secret"}),
    )
    protected.POST("/chat", chatHandler)
}
"""

    endpoint = _endpoint(
        _extract(
            GinAdapter(),
            source,
        )
    )

    assert endpoint.metadata["chat_payload_key"] == "prompt"
    assert endpoint.metadata["auth_required"] is True
    assert endpoint.metadata["auth_type"] == "basic"


def test_echo_infers_message_list_and_global_jwt_middleware() -> None:
    source = """package main

import (
    "github.com/labstack/echo-jwt/v4"
    "github.com/labstack/echo/v4"
)

type ChatMessage struct {
    Role    string `json:"role"`
    Content string `json:"content"`
}

type ChatRequest struct {
    Messages []ChatMessage `json:"messages"`
}

func chatHandler(c echo.Context) error {
    request := new(ChatRequest)
    return c.Bind(request)
}

func main() {
    app := echo.New()
    app.Use(echojwt.WithConfig(echojwt.Config{}))
    app.POST("/chat", chatHandler)
}
"""

    detections = _extract(
        EchoAdapter(),
        source,
    )
    endpoint = _endpoint(detections)
    auth = _by_type(
        detections,
        ComponentType.AUTH,
    )

    assert endpoint.metadata["request_body_schema"] == {
        "messages": "[]ChatMessage",
    }
    assert endpoint.metadata["chat_payload_key"] == "messages"
    assert endpoint.metadata["chat_payload_list"] is True
    assert endpoint.metadata["auth_required"] is True
    assert endpoint.metadata["auth_type"] == "jwt"

    assert len(auth) == 1
    assert auth[0].metadata["auth_type"] == "jwt"
    assert auth[0].snippet == "echojwt.WithConfig(...)"


def test_net_http_infers_schema_from_json_decoder() -> None:
    source = """package main

import (
    "encoding/json"
    "net/http"
)

type ChatRequest struct {
    Query     string `json:"query"`
    SessionID string `json:"session_id,omitempty"`
}

func chatHandler(w http.ResponseWriter, r *http.Request) {
    request := ChatRequest{}
    _ = json.NewDecoder(r.Body).Decode(&request)
}

func main() {
    mux := http.NewServeMux()
    mux.Handle("/chat", http.HandlerFunc(chatHandler))
}
"""

    detections = _extract(
        NetHTTPAdapter(),
        source,
    )
    endpoint = _endpoint(detections)

    assert endpoint.metadata["request_body_schema"] == {
        "query": "string",
        "session_id": "string",
    }
    assert endpoint.metadata["chat_payload_key"] == "query"
    assert endpoint.metadata["chat_payload_list"] is False
    assert endpoint.metadata["auth_required"] is False
    assert endpoint.metadata["accepts_user_input"] is True
    assert (
        _by_type(
            detections,
            ComponentType.AUTH,
        )
        == []
    )


def test_chi_infers_schema_from_json_decoder() -> None:
    source = """package main

import (
    "encoding/json"
    "net/http"

    "github.com/go-chi/chi/v5"
)

type ChatRequest struct {
    Input string `json:"input"`
}

func chatHandler(w http.ResponseWriter, r *http.Request) {
    request := ChatRequest{}
    _ = json.NewDecoder(r.Body).Decode(&request)
}

func main() {
    router := chi.NewRouter()
    router.Post("/chat", chatHandler)
}
"""

    endpoint = _endpoint(
        _extract(
            ChiAdapter(),
            source,
        )
    )

    assert endpoint.metadata["request_body_schema"] == {
        "input": "string",
    }
    assert endpoint.metadata["chat_payload_key"] == "input"
    assert endpoint.metadata["auth_required"] is False


def test_inline_gin_handler_is_supported() -> None:
    source = """package main

import "github.com/gin-gonic/gin"

type ChatRequest struct {
    UserMessage string `json:"user_message"`
}

func main() {
    router := gin.New()
    router.POST("/chat", func(c *gin.Context) {
        request := &ChatRequest{}
        _ = c.ShouldBind(request)
    })
}
"""

    endpoint = _endpoint(
        _extract(
            GinAdapter(),
            source,
        )
    )

    assert endpoint.metadata["request_body_schema"] == {
        "user_message": "string",
    }
    assert endpoint.metadata["chat_payload_key"] == "user_message"
    assert endpoint.metadata["auth_required"] is False


def test_gin_jwt_middleware_variable_protects_later_route() -> None:
    source = """package main

import (
    jwt "github.com/appleboy/gin-jwt/v2"
    "github.com/gin-gonic/gin"
)

func main() {
    authMiddleware, _ := jwt.New(
        &jwt.GinJWTMiddleware{},
    )
    router := gin.New()
    router.Use(
        authMiddleware.MiddlewareFunc(),
    )
    router.POST("/chat", chatHandler)
}
"""

    detections = _extract(
        GinAdapter(),
        source,
    )
    endpoint = _endpoint(detections)
    auth = _by_type(
        detections,
        ComponentType.AUTH,
    )

    assert endpoint.metadata["auth_required"] is True
    assert endpoint.metadata["auth_type"] == "jwt"
    assert len(auth) == 1
    assert auth[0].snippet == "authMiddleware.MiddlewareFunc(...)"


def test_unrecognized_middleware_and_external_handler_are_conservative() -> None:
    source = """package main

import "github.com/gin-gonic/gin"

func main() {
    router := gin.New()
    router.Use(custom.BasicAuth())
    router.POST(
        "/chat",
        external.ChatHandler,
    )
}
"""

    detections = _extract(
        GinAdapter(),
        source,
    )
    endpoint = _endpoint(detections)

    assert endpoint.metadata["request_body_schema"] == {}
    assert endpoint.metadata["chat_payload_key"] is None
    assert endpoint.metadata["chat_payload_list"] is False
    assert endpoint.metadata["auth_required"] is False
    assert (
        _by_type(
            detections,
            ComponentType.AUTH,
        )
        == []
    )


def test_middleware_after_route_does_not_retroactively_protect_it() -> None:
    source = """package main

import "github.com/gin-gonic/gin"

func main() {
    router := gin.New()
    router.POST("/chat", chatHandler)
    router.Use(
        gin.BasicAuth(
            gin.Accounts{"admin": "secret"},
        ),
    )
}
"""

    detections = _extract(
        GinAdapter(),
        source,
    )
    endpoint = _endpoint(detections)

    assert endpoint.metadata["auth_required"] is False
    assert (
        _by_type(
            detections,
            ComponentType.AUTH,
        )
        == []
    )


def test_simple_routes_keep_empty_metadata_fields() -> None:
    source = """package main

import "github.com/labstack/echo/v4"

func main() {
    app := echo.New()
    app.GET("/health", healthHandler)
}
"""

    endpoint = _endpoint(
        _extract(
            EchoAdapter(),
            source,
        )
    )

    assert endpoint.metadata["request_body_schema"] == {}
    assert endpoint.metadata["chat_payload_key"] is None
    assert endpoint.metadata["chat_payload_list"] is False
    assert endpoint.metadata["auth_required"] is False
    assert "accepts_user_input" not in endpoint.metadata
