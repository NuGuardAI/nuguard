"""Unit tests for nuguard.common.browser_login.yaml_writer.

Requires ruamel.yaml (the `browser` extra) — skipped automatically if it is
not installed, matching this module's lazy-import contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ruamel_yaml = pytest.importorskip("ruamel.yaml", reason="ruamel.yaml (browser extra) not installed")

from nuguard.common.browser_login.yaml_writer import (  # noqa: E402
    apply_target_updates,
    dump_to_string,
    load_editable_yaml,
    render_diff,
    write_yaml,
)
from nuguard.common.errors import BrowserLoginError  # noqa: E402

# A realistic, heavily commented nuguard.yaml fragment — mirrors the trailing
# dangling-comment shape found in tests/apps/kscope/nuguard.yaml that caused
# a real comment-loss bug during development of this module (a naive
# `target["auth"] = new_mapping` discarded every comment between the old
# auth block and the next top-level section).
FIXTURE = """\
sbom: ./app.sbom.json

target:
  url: https://example.com/

  # Authentication notes explaining the auth choice in detail,
  # spanning several lines, that must survive any rewrite.
  auth:
    type: basic
    username: alice
    password: secret

  # Dangling comment block between auth and the next top-level section —
  # this is the exact shape that was being silently dropped.
  # endpoint: /api/chat
  # chat_payload_extras:
  #   user_id: alice

behavior:
  llm: true
"""


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "nuguard.yaml"
    p.write_text(FIXTURE, encoding="utf-8")
    return p


class TestApplyTargetUpdates:
    def test_replaces_auth_block_with_cookie_file(self, yaml_path: Path) -> None:
        editable = load_editable_yaml(yaml_path)
        apply_target_updates(editable, cookie_file="./cookies.txt", chat_payload_extras={})
        after = dump_to_string(editable)
        assert "type: cookie_file" in after
        assert "cookie_file: ./cookies.txt" in after
        assert "username: alice" not in after

    def test_preserves_dangling_comment_after_auth_block(self, yaml_path: Path) -> None:
        """Regression test for the comment-loss bug found while building this
        feature: replacing target['auth'] wholesale must not discard the
        trailing comment block that followed it."""
        editable = load_editable_yaml(yaml_path)
        apply_target_updates(editable, cookie_file="./cookies.txt", chat_payload_extras={})
        after = dump_to_string(editable)
        assert "Dangling comment block between auth and the next top-level section" in after
        assert "# endpoint: /api/chat" in after

    def test_preserves_unrelated_comments_elsewhere_in_file(self, yaml_path: Path) -> None:
        editable = load_editable_yaml(yaml_path)
        apply_target_updates(editable, cookie_file="./cookies.txt", chat_payload_extras={})
        after = dump_to_string(editable)
        assert "Authentication notes explaining the auth choice" in after

    def test_merges_chat_payload_extras_without_clobbering_existing_keys(self, tmp_path: Path) -> None:
        text = FIXTURE.replace(
            "behavior:", "  chat_payload_extras:\n    tenant_id: acme\n\nbehavior:"
        )
        p = tmp_path / "nuguard.yaml"
        p.write_text(text, encoding="utf-8")
        editable = load_editable_yaml(p)
        apply_target_updates(
            editable, cookie_file="./cookies.txt", chat_payload_extras={"consumerID": "abc123"}
        )
        after = dump_to_string(editable)
        assert "tenant_id: acme" in after
        assert "consumerID: abc123" in after

    def test_ambiguous_extras_are_not_auto_written_but_noted(self, yaml_path: Path) -> None:
        editable = load_editable_yaml(yaml_path)
        summary = apply_target_updates(
            editable,
            cookie_file="./cookies.txt",
            chat_payload_extras={},
            ambiguous_extras={"tenantId": ["t-1", "t-2"]},
        )
        after = dump_to_string(editable)
        # No real "tenantId: <value>" key/value assignment was written — the
        # candidate only appears inside a comment, never as live YAML.
        assert not any(
            line.strip().startswith("tenantId:") for line in after.splitlines()
        )
        assert "tenantId" in after  # mentioned in the explanatory comment
        assert any("unconfirmed candidate" in line for line in summary)

    def test_summary_reports_old_and_new_auth_type(self, yaml_path: Path) -> None:
        editable = load_editable_yaml(yaml_path)
        summary = apply_target_updates(editable, cookie_file="./cookies.txt", chat_payload_extras={})
        assert any("basic -> cookie_file" in line for line in summary)

    def test_round_trip_with_no_mutation_is_a_no_op(self, yaml_path: Path) -> None:
        """Sanity check that dumping without mutating reproduces content
        equivalent to the source (formatting settings chosen specifically to
        avoid reflowing lists/long strings)."""
        editable = load_editable_yaml(yaml_path)
        before = dump_to_string(editable)
        diff = render_diff(FIXTURE, before)
        assert diff == ""


class TestWriteYaml:
    def test_writes_file_and_applies_changes(self, yaml_path: Path) -> None:
        editable = load_editable_yaml(yaml_path)
        apply_target_updates(editable, cookie_file="./cookies.txt", chat_payload_extras={})
        write_yaml(editable)
        on_disk = yaml_path.read_text(encoding="utf-8")
        assert "type: cookie_file" in on_disk

    def test_conflict_detected_when_file_changed_since_load(self, yaml_path: Path) -> None:
        editable = load_editable_yaml(yaml_path)
        apply_target_updates(editable, cookie_file="./cookies.txt", chat_payload_extras={})
        # Simulate a concurrent edit after load.
        yaml_path.write_text(FIXTURE + "\n# edited concurrently\n", encoding="utf-8")
        with pytest.raises(BrowserLoginError) as exc_info:
            write_yaml(editable)
        assert exc_info.value.step == "yaml_write_conflict"
        # The concurrent edit must not have been clobbered.
        assert "edited concurrently" in yaml_path.read_text(encoding="utf-8")

    def test_missing_file_raises_browser_login_error(self, tmp_path: Path) -> None:
        with pytest.raises(BrowserLoginError) as exc_info:
            load_editable_yaml(tmp_path / "does-not-exist.yaml")
        assert exc_info.value.step == "yaml_write"
