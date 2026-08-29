"""Comment-preserving read/mutate/write helpers for nuguard.yaml.

Used only by ``nuguard target discover-browser`` to surgically update the
``target.auth`` / ``target.chat_payload_extras`` keys after a successful
browser login, without disturbing the rest of a hand-authored, heavily
commented nuguard.yaml file (real examples routinely carry multi-paragraph
comments explaining *why* a given auth choice was made — see
``tests/apps/kscope/nuguard.yaml``). Plain PyYAML (used elsewhere in
``nuguard/config.py`` for reading config) round-trips through a bare dict and
would silently discard all of that on a rewrite, which is why this module
uses ``ruamel.yaml``'s round-trip mode instead — a separate, comment-aware
YAML library, imported lazily so the base install never requires it.

No other part of NuGuard reads or writes nuguard.yaml through this module;
``nuguard/config.py``'s ``load_config()`` is untouched and keeps using PyYAML
for normal config loading.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nuguard.common.errors import BrowserLoginError
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from ruamel.yaml.comments import CommentedMap

_log = get_logger(__name__)


def _require_ruamel() -> Any:
    try:
        from ruamel.yaml import YAML  # noqa: PLC0415
    except ImportError as exc:
        raise BrowserLoginError(
            "ruamel.yaml is not installed.",
            step="playwright_not_installed",  # same remediation: pip install nuguard[browser]
            cause=str(exc),
        ) from exc
    yaml = YAML()
    yaml.preserve_quotes = True
    # sequence=4/offset=2 matches conventional "  - item" block-sequence
    # indentation (the pattern nuguard.yaml.example and every real config in
    # this repo uses) — the ruamel default (sequence=2/offset=0) silently
    # re-indents every list in the file on dump, which is a surprising diff
    # for a tool whose whole point is a minimal, reviewable change. A very
    # large width disables ruamel's line-wrapping of long scalar values,
    # which otherwise reflows unrelated long strings (e.g. defence_regression
    # messages) even when they weren't touched by this write.
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


class EditableYamlDoc:
    """A loaded nuguard.yaml, tracked for the concurrent-edit guard used by write_yaml()."""

    def __init__(self, path: Path, doc: "CommentedMap", loaded_mtime_ns: int) -> None:
        self.path = path
        self.doc = doc
        self.loaded_mtime_ns = loaded_mtime_ns


def load_editable_yaml(path: Path) -> EditableYamlDoc:
    yaml = _require_ruamel()
    if not path.exists():
        raise BrowserLoginError(
            f"nuguard.yaml not found at {path}.",
            step="yaml_write",
            cause="file not found",
        )
    mtime_ns = path.stat().st_mtime_ns
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.load(fh)
    return EditableYamlDoc(path=path, doc=doc, loaded_mtime_ns=mtime_ns)


def _replace_child_mapping(parent: "CommentedMap", key: str, new_child: "CommentedMap") -> None:
    """Replace ``parent[key]`` with ``new_child``, preserving any trailing
    "dangling" comment block attached to the *old* child mapping.

    ruamel.yaml stores comments that appear between the end of a nested
    mapping's last key and the next sibling key (at a shallower indent) as a
    trailing comment attached to the *old* mapping's last key — not to the
    parent. A naive ``parent[key] = new_child`` therefore silently discards
    every comment line between the old block and whatever came after it.
    This is exactly the failure mode this module exists to avoid (see the
    module docstring), so every wholesale child-mapping replacement in this
    file must go through here rather than a bare assignment.
    """
    old_child = parent.get(key)
    trailing = None
    if hasattr(old_child, "ca") and old_child.ca.items:
        last_key = list(old_child.ca.items.keys())[-1]
        trailing = old_child.ca.items.get(last_key)
    parent[key] = new_child
    if trailing is not None and len(new_child):
        new_last_key = list(new_child.keys())[-1]
        new_child.ca.items[new_last_key] = trailing


def apply_target_updates(
    editable: EditableYamlDoc,
    *,
    cookie_file: str,
    chat_payload_extras: dict[str, str],
    ambiguous_extras: dict[str, list[str]] | None = None,
    endpoint: str | None = None,
) -> list[str]:
    """Mutate ``editable.doc['target']`` in place. Returns a list of
    human-readable summary lines describing what changed, for the CLI's diff
    preview.

    ``target.auth`` is a single logical, mutually-exclusive setting (one auth
    type at a time) — its *keys* are replaced wholesale with the new
    cookie_file block rather than trying to auto-preserve the old
    basic/login_flow fields (the user can review exactly what changed via
    the diff/`git diff`, the same way the equivalent hand-edit was reviewed
    for kscope), but any trailing comment block that followed the old
    ``auth:`` mapping is carried over via ``_replace_child_mapping`` so
    unrelated documentation comments elsewhere in the file survive.
    """
    from ruamel.yaml.comments import CommentedMap  # noqa: PLC0415

    doc = editable.doc
    summary: list[str] = []

    target = doc.get("target")
    if not isinstance(target, CommentedMap):
        target = CommentedMap()
        doc["target"] = target

    old_auth_type = None
    if isinstance(target.get("auth"), dict):
        old_auth_type = target["auth"].get("type")

    new_auth = CommentedMap()
    new_auth["type"] = "cookie_file"
    new_auth["cookie_file"] = cookie_file
    _replace_child_mapping(target, "auth", new_auth)
    summary.append(
        f"target.auth: {old_auth_type or '(unset)'} -> cookie_file (cookie_file: {cookie_file})"
    )

    if chat_payload_extras:
        extras = target.get("chat_payload_extras")
        if not isinstance(extras, CommentedMap):
            extras = CommentedMap()
            target["chat_payload_extras"] = extras
        for key, value in chat_payload_extras.items():
            old_value = extras.get(key)
            extras[key] = value
            if old_value != value:
                summary.append(f"target.chat_payload_extras.{key}: {old_value!r} -> {value!r}")

    if ambiguous_extras:
        lines = [f"#   {k}: candidates observed = {v}" for k, v in ambiguous_extras.items()]
        summary.append(
            "target.chat_payload_extras: unconfirmed candidate field(s) NOT written "
            "automatically (see nuguard.yaml comment):\n" + "\n".join(lines)
        )
        if not isinstance(target.get("chat_payload_extras"), CommentedMap):
            target["chat_payload_extras"] = CommentedMap()
        comment_lines = [
            "discover-browser found additional candidate field(s) it could not confirm:",
            *[f"  {k}: observed value(s) = {v}" for k, v in ambiguous_extras.items()],
            "Uncomment and set the correct one manually if the chat endpoint needs it.",
        ]
        # Attach the comment on the PARENT mapping, before the
        # chat_payload_extras key itself — attaching it inside the (possibly
        # still-empty) extras mapping only renders when that mapping already
        # has at least one real key, which is not guaranteed here (no
        # confirmed fields yet, only ambiguous ones).
        target.yaml_set_comment_before_after_key(
            key="chat_payload_extras", before="\n".join(comment_lines)
        )

    if endpoint and endpoint != target.get("endpoint"):
        old_endpoint = target.get("endpoint")
        target["endpoint"] = endpoint
        summary.append(f"target.endpoint: {old_endpoint!r} -> {endpoint!r}")

    return summary


def render_diff(before_text: str, after_text: str, filename: str = "nuguard.yaml") -> str:
    diff = difflib.unified_diff(
        before_text.splitlines(keepends=True),
        after_text.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


def dump_to_string(editable: EditableYamlDoc) -> str:
    import io

    yaml = _require_ruamel()
    buf = io.StringIO()
    yaml.dump(editable.doc, buf)
    return buf.getvalue()


def write_yaml(editable: EditableYamlDoc) -> None:
    """Atomically write the (mutated) document back to its original path.

    Re-checks the file's mtime immediately before replacing to detect a
    concurrent edit (e.g. the user hand-editing nuguard.yaml while discovery
    was running) and aborts rather than silently clobbering it.
    """
    path = editable.path
    try:
        current_mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        raise BrowserLoginError(
            f"Failed to stat {path} before write: {exc}",
            step="yaml_write",
            cause=str(exc),
        ) from exc

    if current_mtime_ns != editable.loaded_mtime_ns:
        raise BrowserLoginError(
            "nuguard.yaml was modified since discovery started — not overwriting. "
            "Re-run 'nuguard target discover-browser --write'.",
            step="yaml_write_conflict",
        )

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        text = dump_to_string(editable)
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            _log.debug("yaml_writer: failed to clean up temp file %s", tmp_path, exc_info=True)
        raise BrowserLoginError(
            f"Failed to write nuguard.yaml: {exc}",
            step="yaml_write",
            cause=str(exc),
        ) from exc
