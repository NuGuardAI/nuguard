"""Prompt SQL adapter — detects system prompts / prompt templates seeded in SQL.

Some applications store their system prompt or prompt template in a database
table (e.g. a ``prompts`` or ``system_prompts`` table) rather than as a Python
constant. This adapter scans ``.sql`` files for two patterns that seed such
content:

``CREATE TABLE`` column defaults
    A column definition with a ``DEFAULT '...'`` clause whose column name
    looks prompt-related, e.g.::

        CREATE TABLE prompts (
            id INT PRIMARY KEY,
            template TEXT DEFAULT 'You are a helpful assistant...'
        );

``INSERT INTO`` seed rows
    A literal seed insert into a prompt-related table/column, e.g.::

        INSERT INTO system_prompts (name, content)
        VALUES ('default', 'You are a helpful assistant...');

Emits ``PROMPT`` nodes using the same ``metadata`` key vocabulary as the
Python prompt extractors in ``nuguard.sbom.extractor.core`` (``role``,
``content``, ``char_count``, ``is_template``, ``template_variables``) so
downstream code that reads ``metadata.extras`` needs no SQL-specific
handling.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable

from ..types import ComponentType
from .base import ComponentDetection

_CONFIDENCE = 0.85
_MAX_CONTENT_LEN = 500
_MIN_CONTENT_LEN = 20  # skip short placeholder-ish defaults/seeds
_MAX_SEED_ROWS_PER_COLUMN = 3  # cap seed-insert nodes to avoid explosion on large fixtures

# Table/column names that suggest prompt-related content. Mirrors the naming
# convention used by nuguard.sbom.extractor.core's Python prompt constant/dict
# extractors (_PROMPT_CONST_NAME_RE / prompt dict name regex).
_PROMPT_NAME_RE = re.compile(
    r"prompt|persona|instruction|system_message",
    re.IGNORECASE,
)
# Generic "the actual text lives here" column names — only treated as
# prompt-relevant when the *table* (not the column itself) matches
# _PROMPT_NAME_RE, e.g. `content`/`template` under a `prompts` table.
# Deliberately excludes identifier-ish names (id, name, created_at, ...) so a
# table simply being named "prompts" doesn't flag every column in it.
_GENERIC_CONTENT_COLUMN_RE = re.compile(
    r"^(?:content|text|template|value|message|body)$",
    re.IGNORECASE,
)


def _is_prompt_column(table_name: str, column_name: str) -> bool:
    """True if *column_name* looks like it holds prompt/template content.

    A column name matching ``_PROMPT_NAME_RE`` directly (e.g. ``system_prompt``,
    ``persona``) always qualifies. A generic content-ish column name (``content``,
    ``template``, ...) only qualifies when the *table* name is itself
    prompt-related (e.g. ``content`` under ``system_prompts``) — this avoids
    flagging unrelated columns (``id``, ``created_at``) just because the table
    happens to be named ``prompts``.
    """
    if _PROMPT_NAME_RE.search(column_name):
        return True
    return bool(_PROMPT_NAME_RE.search(table_name) and _GENERIC_CONTENT_COLUMN_RE.match(column_name))

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}|:([a-zA-Z_][a-zA-Z0-9_]*)\b|%s")

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:\w+\.)?\"?(\w+)\"?\s*\(",
    re.IGNORECASE,
)
_CONSTRAINT_RE = re.compile(
    r"^\s*(?:PRIMARY|FOREIGN|UNIQUE|CHECK|INDEX|KEY|CONSTRAINT)\b",
    re.IGNORECASE,
)
# column_name TYPE ... DEFAULT '...'  (single- or double-quoted default value)
_COLUMN_DEFAULT_RE = re.compile(
    r"""^\s*"?(?P<col>\w+)"?\s+\w+[\w()]*\s+.*?DEFAULT\s+'(?P<value>(?:[^'\\]|\\.)*)'""",
    re.IGNORECASE | re.DOTALL,
)

_INSERT_RE = re.compile(
    r"""INSERT\s+INTO\s+(?:\w+\.)?"?(?P<table>\w+)"?\s*
        \((?P<columns>[^)]+)\)\s*
        VALUES\s*""",
    re.IGNORECASE | re.VERBOSE,
)
_STRING_LITERAL_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")


def _extract_template_vars(text: str) -> list[str]:
    names: list[str] = []
    for m in _TEMPLATE_VAR_RE.finditer(text):
        name = m.group(1) or m.group(2)
        if name and name not in names:
            names.append(name)
    return names


def _unescape_sql_string(raw: str) -> str:
    return raw.replace("\\'", "'").replace("''", "'")


def _split_sql_tuple_values(tuple_body: str) -> list[str]:
    """Split a VALUES ``(...)`` tuple body into raw per-column value strings.

    Splits on top-level commas only — commas inside a single-quoted string
    literal do not split — so the result stays positionally aligned with the
    INSERT statement's column list even when some values are unquoted
    (numbers, ``NULL``, ...).
    """
    values: list[str] = []
    buf: list[str] = []
    in_quote = False
    i = 0
    n = len(tuple_body)
    while i < n:
        ch = tuple_body[i]
        if in_quote:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and tuple_body[i + 1] == "'":
                    # Escaped '' inside a string literal — consume both chars.
                    buf.append(tuple_body[i + 1])
                    i += 1
                else:
                    in_quote = False
        elif ch == "'":
            in_quote = True
            buf.append(ch)
        elif ch == ",":
            values.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        values.append("".join(buf).strip())
    return values


class PromptSQLAdapter:
    """Scans SQL files for prompt/template content seeded via DDL defaults or seed inserts.

    This is a *file adapter* (not a ``FrameworkAdapter``) because SQL files
    carry no Python import information. The extractor calls ``scan()``
    directly for ``.sql`` files, alongside ``DataClassificationSQLAdapter``.
    """

    name = "prompt_sql"
    priority = 5

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []
        nl_offsets = [m.start() for m in re.finditer(r"\n", content)]

        def line_at(pos: int) -> int:
            return bisect_right(nl_offsets, pos) + 1

        detections.extend(self._scan_create_table_defaults(content, file_path, line_at))
        detections.extend(self._scan_insert_seeds(content, file_path, line_at))
        return detections

    def _scan_create_table_defaults(
        self, content: str, file_path: str, line_at: Callable[[int], int]
    ) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []

        for table_match in _CREATE_TABLE_RE.finditer(content):
            table_name = table_match.group(1)
            start = table_match.end()

            depth, pos = 1, start
            while pos < len(content) and depth > 0:
                ch = content[pos]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                pos += 1
            table_body = content[start : pos - 1]

            offset = start
            for line in table_body.splitlines(keepends=True):
                stripped = line.strip().rstrip(",")
                line_start_offset = offset
                offset += len(line)
                if not stripped or stripped.startswith("--"):
                    continue
                if _CONSTRAINT_RE.match(stripped):
                    continue

                m = _COLUMN_DEFAULT_RE.match(stripped)
                if not m:
                    continue
                col_name = m.group("col")
                if not _is_prompt_column(table_name, col_name):
                    continue
                value = _unescape_sql_string(m.group("value"))
                if len(value) < _MIN_CONTENT_LEN:
                    continue

                template_vars = _extract_template_vars(value)
                detections.append(self._make_detection(
                    table_name=table_name,
                    column_name=col_name,
                    content=value,
                    template_vars=template_vars,
                    source="sql_default",
                    file_path=file_path,
                    line=line_at(line_start_offset),
                ))

        return detections

    def _scan_insert_seeds(
        self, content: str, file_path: str, line_at: Callable[[int], int]
    ) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []
        seed_counts: dict[tuple[str, str], int] = {}

        for insert_match in _INSERT_RE.finditer(content):
            table_name = insert_match.group("table")
            columns = [c.strip().strip('"') for c in insert_match.group("columns").split(",")]

            prompt_col_idxs = [
                i for i, col in enumerate(columns)
                if _is_prompt_column(table_name, col)
            ]
            if not prompt_col_idxs:
                continue

            # Walk forward from VALUES to the matching closing paren of the
            # first values tuple. Multiple ``(...), (...)`` tuples are each
            # scanned in turn until the row cap is reached.
            pos = insert_match.end()
            while pos < len(content):
                # Skip to the next '(' that opens a values tuple.
                open_idx = content.find("(", pos)
                if open_idx == -1:
                    break
                depth, p = 1, open_idx + 1
                while p < len(content) and depth > 0:
                    ch = content[p]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    p += 1
                tuple_body = content[open_idx + 1 : p - 1]

                raw_values = _split_sql_tuple_values(tuple_body)
                for idx in prompt_col_idxs:
                    if idx >= len(raw_values):
                        continue
                    lit_match = _STRING_LITERAL_RE.fullmatch(raw_values[idx])
                    if not lit_match:
                        continue  # unquoted (numeric, NULL, ...) — not prompt content
                    col_name = columns[idx]
                    key = (table_name, col_name)
                    count = seed_counts.get(key, 0)
                    if count >= _MAX_SEED_ROWS_PER_COLUMN:
                        continue
                    value = _unescape_sql_string(lit_match.group(1))
                    if len(value) < _MIN_CONTENT_LEN:
                        continue
                    seed_counts[key] = count + 1
                    template_vars = _extract_template_vars(value)
                    detections.append(self._make_detection(
                        table_name=table_name,
                        column_name=col_name,
                        content=value,
                        template_vars=template_vars,
                        source="sql_seed",
                        file_path=file_path,
                        line=line_at(open_idx),
                        row_suffix=f":row{count}" if count > 0 else "",
                    ))

                # Another tuple follows only if the next non-whitespace char is a comma.
                nxt = content[p:].lstrip()
                if not nxt.startswith(","):
                    break
                pos = p

        return detections

    def _make_detection(
        self,
        *,
        table_name: str,
        column_name: str,
        content: str,
        template_vars: list[str],
        source: str,
        file_path: str,
        line: int,
        row_suffix: str = "",
    ) -> ComponentDetection:
        truncated = content[:_MAX_CONTENT_LEN]
        is_system = bool(re.search(r"system", f"{table_name}_{column_name}", re.IGNORECASE))
        return ComponentDetection(
            component_type=ComponentType.PROMPT,
            canonical_name=f"prompt:sql:{table_name.lower()}:{column_name.lower()}{row_suffix}",
            display_name=f"{table_name}.{column_name}",
            adapter_name=self.name,
            priority=self.priority,
            confidence=_CONFIDENCE,
            metadata={
                "role": "system" if is_system else "unspecified",
                "content": truncated,
                "char_count": len(content),
                "is_template": bool(template_vars),
                "template_variables": template_vars,
                "source": source,
                "table_name": table_name,
                "column_name": column_name,
            },
            file_path=file_path,
            line=line,
            snippet=truncated[:80] + ("..." if len(truncated) > 80 else ""),
            evidence_kind="regex",
        )
