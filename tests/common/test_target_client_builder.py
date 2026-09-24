"""Unit tests for nuguard.common.target_client_builder."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from nuguard.common.target_client_builder import build_target_app_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_client():
    """Context manager that mocks TargetAppClient at the module level."""
    return patch("nuguard.redteam.target.client.TargetAppClient")


def _patch_framework_adapter(return_value=None):
    return patch(
        "nuguard.redteam.target.framework_adapters.factory.make_framework_adapter",
        return_value=return_value,
    )


def _patch_discover(return_value=("/chat", "message", False, None)):
    return patch(
        "nuguard.common.endpoint_detection.sbom.discover_chat_config_from_sbom",
        return_value=return_value,
    )


# ---------------------------------------------------------------------------
# No-SBOM (baseline) tests
# ---------------------------------------------------------------------------


class TestNoSbom:
    def test_returns_target_app_client_instance(self) -> None:
        with _patch_client() as MockClient:
            result = build_target_app_client("http://app.test")
        MockClient.assert_called_once()
        assert result is MockClient.return_value

    def test_default_endpoint(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test")
        _, kwargs = MockClient.call_args
        assert kwargs["chat_path"] == "/chat"

    def test_default_payload_key(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test")
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_key"] == "message"

    def test_default_payload_format(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test")
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_format"] == "json"

    def test_default_response_key_is_none(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test")
        _, kwargs = MockClient.call_args
        assert kwargs["chat_response_key"] is None

    def test_default_framework_adapter_is_none(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test")
        _, kwargs = MockClient.call_args
        assert kwargs["framework_adapter"] is None

    def test_base_url_forwarded(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://custom.host:9000")
        _, kwargs = MockClient.call_args
        assert kwargs["base_url"] == "http://custom.host:9000"

    def test_custom_timeout(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test", timeout=30.0)
        _, kwargs = MockClient.call_args
        assert kwargs["timeout"] == 30.0

    def test_auth_headers_forwarded(self) -> None:
        headers = {"Authorization": "Bearer tok123"}
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test", auth_headers=headers)
        _, kwargs = MockClient.call_args
        assert kwargs["default_headers"] == headers

    def test_no_auth_headers_passes_none(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test")
        _, kwargs = MockClient.call_args
        assert kwargs["default_headers"] is None

    def test_explicit_endpoint_used(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test", endpoint="/api/chat")
        _, kwargs = MockClient.call_args
        assert kwargs["chat_path"] == "/api/chat"

    def test_explicit_payload_key_used(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test", payload_key="query")
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_key"] == "query"

    def test_payload_list_forwarded(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test", payload_list=True)
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_list"] is True

    def test_form_payload_format(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client("http://app.test", payload_format="form")
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_format"] == "form"

    def test_explicit_response_key_used(self) -> None:
        with _patch_client() as MockClient:
            build_target_app_client(
                "http://app.test",
                response_key="answer",
                explicitly_set={"chat_response_key"},
            )
        _, kwargs = MockClient.call_args
        assert kwargs["chat_response_key"] == "answer"


# ---------------------------------------------------------------------------
# SBOM + framework adapter tests
# ---------------------------------------------------------------------------


class TestFrameworkAdapterDetection:
    def test_framework_adapter_used_when_sbom_present(self, minimal_sbom_doc) -> None:
        mock_adapter = MagicMock()
        mock_adapter.run_path = "/adk/run"
        with _patch_client() as MockClient, _patch_framework_adapter(mock_adapter), _patch_discover():
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["framework_adapter"] is mock_adapter

    def test_framework_adapter_endpoint_used_when_endpoint_empty(self, minimal_sbom_doc) -> None:
        mock_adapter = MagicMock()
        mock_adapter.run_path = "/adk/run"
        with _patch_client() as MockClient, _patch_framework_adapter(mock_adapter), _patch_discover(("/adk/run", "message", False, None)):
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["chat_path"] == "/adk/run"

    def test_no_adapter_when_sbom_result_is_none(self, minimal_sbom_doc) -> None:
        with _patch_client() as MockClient, _patch_framework_adapter(None), _patch_discover():
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["framework_adapter"] is None

    def test_framework_adapter_failure_is_ignored(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            patch(
                "nuguard.redteam.target.framework_adapters.factory.make_framework_adapter",
                side_effect=RuntimeError("adapter boom"),
            ),
            _patch_discover(),
        ):
            # Should not raise
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        MockClient.assert_called_once()


# ---------------------------------------------------------------------------
# SBOM-based endpoint / payload discovery tests
# ---------------------------------------------------------------------------


class TestSbomDiscovery:
    def test_sbom_endpoint_discovery_applied(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            _patch_discover(("/api/chat/queue", "message", False, None)),
        ):
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["chat_path"] == "/api/chat/queue"

    def test_sbom_payload_key_discovery_applied(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            _patch_discover(("/chat", "query", False, None)),
        ):
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_key"] == "query"

    def test_explicit_endpoint_not_overridden_by_sbom(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            _patch_discover(("/discovered/path", "message", False, None)),
        ):
            build_target_app_client(
                "http://app.test",
                endpoint="/explicit/path",
                sbom=minimal_sbom_doc,
                explicitly_set={"target_endpoint"},
            )
        _, kwargs = MockClient.call_args
        assert kwargs["chat_path"] == "/explicit/path"

    def test_explicit_payload_key_not_overridden_by_sbom(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            _patch_discover(("/chat", "discovered_key", False, None)),
        ):
            build_target_app_client(
                "http://app.test",
                payload_key="my_key",
                sbom=minimal_sbom_doc,
                explicitly_set={"chat_payload_key"},
            )
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_key"] == "my_key"

    def test_sbom_response_key_discovery_applied(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            _patch_discover(("/chat", "message", False, "response")),
        ):
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["chat_response_key"] == "response"

    def test_explicit_response_key_not_overridden_by_sbom(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            _patch_discover(("/chat", "message", False, "discovered_key")),
        ):
            build_target_app_client(
                "http://app.test",
                response_key="explicit_key",
                sbom=minimal_sbom_doc,
                explicitly_set={"chat_response_key"},
            )
        _, kwargs = MockClient.call_args
        assert kwargs["chat_response_key"] == "explicit_key"

    def test_sbom_discovery_failure_is_ignored(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            patch(
                "nuguard.common.endpoint_detection.sbom.discover_chat_config_from_sbom",
                side_effect=RuntimeError("discovery boom"),
            ),
        ):
            # Should not raise; falls back to defaults
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["chat_path"] == "/chat"
        assert kwargs["chat_payload_key"] == "message"

    def test_sbom_payload_list_discovery_applied(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            _patch_framework_adapter(None),
            _patch_discover(("/chat", "messages", True, None)),
        ):
            build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        _, kwargs = MockClient.call_args
        assert kwargs["chat_payload_list"] is True


class TestWebSocketDiscovery:
    def test_websocket_marker_builds_ws_client_instead_of_http(self, minimal_sbom_doc) -> None:
        with (
            _patch_client() as MockClient,
            patch("nuguard.redteam.target.ws_client.WebSocketTargetClient") as MockWsClient,
            _patch_framework_adapter(None),
            _patch_discover(("/ws/chat", "__websocket__", False, None)),
        ):
            result = build_target_app_client("http://app.test", sbom=minimal_sbom_doc)
        MockClient.assert_not_called()
        MockWsClient.assert_called_once()
        _, kwargs = MockWsClient.call_args
        assert kwargs["chat_path"] == "/ws/chat"
        assert result is MockWsClient.return_value

    def test_websocket_client_receives_auth_and_response_complete_options(
        self, minimal_sbom_doc
    ) -> None:
        with (
            _patch_client(),
            patch("nuguard.redteam.target.ws_client.WebSocketTargetClient") as MockWsClient,
            _patch_framework_adapter(None),
            _patch_discover(("/ws/chat", "__websocket__", False, None)),
        ):
            build_target_app_client(
                "http://app.test",
                sbom=minimal_sbom_doc,
                ws_auth_message={"type": "auth", "token": "t"},
                ws_response_complete_key="done",
            )
        _, kwargs = MockWsClient.call_args
        assert kwargs["ws_auth_message"] == {"type": "auth", "token": "t"}


# ---------------------------------------------------------------------------
# Issue #552 regression: real Blissful Store fixture (the app the bug was
# reported against). Uses the REAL make_framework_adapter/_make_ces_adapter
# factory logic (only TargetAppClient itself is mocked, to avoid a real
# network call) — this is an integration test of the actual selection code,
# not of a mocked stand-in for it.
# ---------------------------------------------------------------------------


def _load_blissful_store_sbom():
    from pathlib import Path

    from nuguard.sbom.serializer import AiSbomSerializer

    path = (
        Path(__file__).parents[2]
        / "tests"
        / "apps"
        / "blissful-store"
        / "reports"
        / "blissfuls-store-sbom-llm-gemini-2.0-flash.json"
    )
    return AiSbomSerializer.from_json(path.read_text(encoding="utf-8"))


class TestIssue552BlissfulStoreRegression:
    """Blissful Store's real, checked-in SBOM reports both google_adk and
    google-ces in summary.frameworks (confirmed CES API_ENDPOINT nodes
    pointing at real ces.googleapis.com session URLs) — exactly the
    combination that, pre-#552, caused behavior/redteam to redirect traffic
    to ces.googleapis.com (and fail on local gcloud auth) instead of the
    app's own configured target, per its nuguard.yaml
    (``target: http://localhost:8081/``)."""

    def test_ces_is_never_selected_for_the_real_proxy_target(self) -> None:
        sbom = _load_blissful_store_sbom()
        assert "google-ces" in sbom.summary.frameworks  # sanity: fixture still has the evidence

        with _patch_client() as MockClient, _patch_discover():
            build_target_app_client("http://localhost:8081/", sbom=sbom)

        _, kwargs = MockClient.call_args
        from nuguard.redteam.target.framework_adapters.google_ces import GoogleCESAdapter

        assert not isinstance(kwargs["framework_adapter"], GoogleCESAdapter)

    def test_client_base_url_stays_the_configured_proxy_not_ces(self) -> None:
        sbom = _load_blissful_store_sbom()

        with _patch_client() as MockClient, _patch_discover():
            build_target_app_client("http://localhost:8081/", sbom=sbom)

        _, kwargs = MockClient.call_args
        assert kwargs["base_url"].rstrip("/") == "http://localhost:8081"


class TestIssue552CesAuthPreflight:
    """CES auth failures must surface immediately as a clear AuthError when
    building the client, not silently or mid-scan (issue #552, item 5)."""

    def _ces_sbom(self) -> MagicMock:
        summary = MagicMock()
        summary.frameworks = ["google-ces"]
        sbom = MagicMock()
        sbom.summary = summary
        sbom.nodes = []
        return sbom

    def test_ces_auth_failure_raises_autherror_immediately(self) -> None:
        from nuguard.common.ces_client import CESAuthError
        from nuguard.common.errors import AuthError

        with (
            _patch_client(),
            patch(
                "nuguard.common.ces_client.get_gcloud_token",
                side_effect=CESAuthError("no gcloud credentials found"),
            ),
        ):
            try:
                build_target_app_client(
                    "https://ces.googleapis.com/v1beta/projects/p",
                    sbom=self._ces_sbom(),
                )
            except AuthError as exc:
                assert exc.identity == "google-ces"
                assert "gcloud" in str(exc)
            else:
                raise AssertionError("expected AuthError to be raised")

    def test_ces_auth_success_builds_client_normally(self) -> None:
        with (
            _patch_client() as MockClient,
            patch("nuguard.common.ces_client.get_gcloud_token", return_value="fake-token"),
        ):
            build_target_app_client(
                "https://ces.googleapis.com/v1beta/projects/p",
                sbom=self._ces_sbom(),
            )
        MockClient.assert_called_once()

    def test_no_preflight_call_for_non_ces_target(self) -> None:
        """A proxy target must never even attempt gcloud auth."""
        with (
            _patch_client(),
            patch("nuguard.common.ces_client.get_gcloud_token") as mock_token,
        ):
            build_target_app_client("http://localhost:8081", sbom=self._ces_sbom())
        mock_token.assert_not_called()
