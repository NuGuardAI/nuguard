from __future__ import annotations

from textwrap import dedent

from nuguard.config import load_config


def test_load_config_maps_redteam_session_bootstrap_fields(tmp_path) -> None:
    config_file = tmp_path / "nuguard.yaml"
    config_file.write_text(
        dedent(
            """
            sbom: ./sbom.json
            redteam:
              target: https://example.test
              target_endpoint: /api/v1/chat
              endpoint_id: chat-endpoint-1
              endpoint_name: Primary Chat
              auth_header: "Authorization: Bearer token"
              session_init_endpoint: /api/v1/new-chat
              session_id_json_key: session_id
              session_header_name: X-Session-ID
              request_timeout: 45
            """
        ).strip()
    )

    cfg = load_config(config_file)

    assert cfg.target_url == "https://example.test"
    assert cfg.target_endpoint == "/api/v1/chat"
    assert cfg.redteam_endpoint_id == "chat-endpoint-1"
    assert cfg.redteam_endpoint_name == "Primary Chat"
    assert cfg.redteam_auth_header == "Authorization: Bearer token"
    assert cfg.redteam_session_init_endpoint == "/api/v1/new-chat"
    assert cfg.redteam_session_id_json_key == "session_id"
    assert cfg.redteam_session_header_name == "X-Session-ID"
    assert cfg.redteam_request_timeout == 45.0