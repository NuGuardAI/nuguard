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
        "nuguard.redteam.executor.orchestrator._discover_chat_config",
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
                "nuguard.redteam.executor.orchestrator._discover_chat_config",
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
