"""Data classification adapter — detects PII and PHI fields in SQL schemas and Python models.

SQL adapter
-----------
Parses ``CREATE TABLE`` statements and classifies column names against a curated
PII/PHI pattern library.  Emits one ``DATASTORE`` node per table that contains at
least one classified column.

Python model adapter
--------------------
Inspects Python class bodies for Pydantic ``BaseModel`` / ``SQLModel``,
SQLAlchemy ORM, and ``@dataclass`` definitions.  Classifies annotated field
names and emits one ``DATASTORE`` node per model that carries PII or PHI data.

Both adapters populate the following node metadata keys:

``data_classification``
    Sorted list of labels detected on the table/model, e.g. ``["PHI", "PII"]``.
``classified_fields``
    Dict mapping each classified field name to its labels,
    e.g. ``{"name": ["PII"], "medical_record_number": ["PHI", "PII"]}``.
``source``
    ``"sql_schema"`` or ``"python_model"`` — indicates detection origin.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from xelo.adapters.base import ComponentDetection, FrameworkAdapter
from xelo.types import ComponentType


# ---------------------------------------------------------------------------
# PII / PHI field pattern library
# ---------------------------------------------------------------------------
# Each entry: compiled pattern matched against lowercase field/column name →
#             sorted list of classification labels ("PII", "PHI", or both).

_FIELD_PATTERNS: list[tuple[re.Pattern[str], list[str]]] = [
    # ── Identity ────────────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(?:full_?name|first_?name|last_?name|display_?name|(?<![a-z])name(?![a-z]))\b"
        ),
        ["PII"],
    ),
    (re.compile(r"\bemail(?:_address)?\b"), ["PII"]),
    (re.compile(r"\b(?:phone(?:_number)?|contact_?number|mobile|telephone|fax)\b"), ["PII"]),
    (
        re.compile(r"\b(?:address|street(?:_address)?|postal_?code|zip(?:_code)?|postcode)\b"),
        ["PII"],
    ),
    (re.compile(r"\b(?:date_?of_?birth|dob|birth_?date|birthdate)\b"), ["PII"]),
    (re.compile(r"\b(?:gender|sex)\b"), ["PII"]),
    (re.compile(r"\bmarital_?status\b"), ["PII"]),
    (re.compile(r"\b(?:nationality|ethnicity|race)\b"), ["PII"]),
    # ── Government / health IDs ──────────────────────────────────────────────
    (
        re.compile(
            r"\b(?:ssn|social_?security|national_?id|passport|driver_?licen[sc]e|licen[sc]e_?number|tax_?id|npi)\b"
        ),
        ["PII"],
    ),
    (
        re.compile(r"\b(?:medical_?record_?number|mrn|record_?number|patient_?id|health_?id)\b"),
        ["PHI", "PII"],
    ),
    # ── Financial ───────────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(?:credit_?card|card_?number|cvv|expiry|bank_?account|account_?number|routing_?number|iban|swift)\b"
        ),
        ["PII"],
    ),
    (re.compile(r"\b(?:income|salary|wage)\b"), ["PII"]),
    # ── Technical / auth PII ────────────────────────────────────────────────
    (re.compile(r"\b(?:ip_?address|mac_?address|device_?id|cookie)\b"), ["PII"]),
    (re.compile(r"\b(?:password|passwd|secret(?:_key)?|private_?key|credential)\b"), ["PII"]),
    # ── PHI / health data (clinical) ─────────────────────────────────────────
    (
        re.compile(r"\b(?:diagnosis|diagnoses|past_?diagnos\w*|medical_?condition|condition)\b"),
        ["PHI"],
    ),
    (re.compile(r"\b(?:prescription|medication|drug|treatment|procedure|therapy)\b"), ["PHI"]),
    (re.compile(r"\b(?:surger(?:y|ies|i\w*)|operation)\b"), ["PHI"]),
    (re.compile(r"\b(?:hospital_?admissions?|admissions?|discharge)\b"), ["PHI"]),
    (re.compile(r"\b(?:immunization\w*|vaccination|vaccine)\b"), ["PHI"]),
    (re.compile(r"\b(?:lab_?result|test_?result|lab_?test|pathology)\b"), ["PHI"]),
    (re.compile(r"\b(?:vital_?sign|blood_?pressure|heart_?rate|weight|height|bmi)\b"), ["PHI"]),
    (re.compile(r"\b(?:blood_?(?:group|type))\b"), ["PHI"]),
    (re.compile(r"\b(?:family_?medical_?history|medical_?history|health_?history)\b"), ["PHI"]),
    (re.compile(r"\b(?:insurance_?(?:id|number|policy)|member_?id|health_?plan)\b"), ["PHI"]),
    (re.compile(r"\b(?:lifestyle_?factor|smoking_?status|alcohol_?use)\b"), ["PHI"]),
    (re.compile(r"\b(?:mental_?health|psychiatric|psychological)\b"), ["PHI"]),
    (re.compile(r"\baller(?:g(?:y|ies|en)|gic)\b"), ["PHI"]),
    (re.compile(r"\b(?:appointment_?reason|chief_?complaint|symptom)\b"), ["PHI"]),
    (re.compile(r"\b(?:prognosis|referral)\b"), ["PHI"]),
]


def classify_fields(field_names: list[str]) -> dict[str, list[str]]:
    """Return ``{field_name: [label, ...]}`` for each field that matches a PII/PHI pattern.

    Fields that match no pattern are omitted.  Labels are sorted alphabetically
    so the output is deterministic (``["PHI", "PII"]`` not ``["PII", "PHI"]``).
    """
    result: dict[str, list[str]] = {}
    for field_name in field_names:
        fn_lower = field_name.lower()
        labels: set[str] = set()
        for pattern, lbls in _FIELD_PATTERNS:
            if pattern.search(fn_lower):
                labels.update(lbls)
        if labels:
            result[field_name] = sorted(labels)
    return result


# ---------------------------------------------------------------------------
# SQL table scanner
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:\w+\.)?\"?(\w+)\"?\s*\(",
    re.IGNORECASE,
)
_COLUMN_LINE_RE = re.compile(r'^\s*"?(\w+)"?\s+\w')
_CONSTRAINT_RE = re.compile(
    r"^\s*(?:PRIMARY|FOREIGN|UNIQUE|CHECK|INDEX|KEY|CONSTRAINT)\b",
    re.IGNORECASE,
)


class DataClassificationSQLAdapter:
    """Scans SQL files for ``CREATE TABLE`` statements and classifies columns.

    This is a *file adapter* (not a ``FrameworkAdapter``) because SQL files
    carry no Python import information.  The extractor calls ``scan()``
    directly for ``.sql`` files.
    """

    name = "data_classification_sql"
    priority = 5  # higher priority than AI framework adapters

    def scan(self, content: str, file_path: str) -> list[ComponentDetection]:
        detections: list[ComponentDetection] = []

        for table_match in _CREATE_TABLE_RE.finditer(content):
            table_name = table_match.group(1)
            start = table_match.end()

            # Walk forward to find the matching closing parenthesis
            depth, pos = 1, start
            while pos < len(content) and depth > 0:
                ch = content[pos]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                pos += 1
            table_body = content[start : pos - 1]

            # Collect column names (skip constraint lines)
            col_names: list[str] = []
            for line in table_body.splitlines():
                line = line.strip().rstrip(",")
                if not line or line.startswith("--"):
                    continue
                if _CONSTRAINT_RE.match(line):
                    continue
                m = _COLUMN_LINE_RE.match(line)
                if m:
                    col_names.append(m.group(1))

            classified = classify_fields(col_names)
            if not classified:
                continue

            all_labels = sorted({lbl for lbls in classified.values() for lbl in lbls})
            line_num = content[: table_match.start()].count("\n") + 1

            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=f"datastore:sql:{table_name.lower()}",
                    display_name=table_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.95,
                    metadata={
                        "adapter": self.name,
                        "table_name": table_name,
                        "source": "sql_schema",
                        "data_classification": all_labels,
                        "classified_fields": classified,
                        "all_columns": col_names,
                    },
                    file_path=file_path,
                    line=line_num,
                    snippet=table_match.group(0)[:500],
                    evidence_kind="ast_instantiation",
                )
            )

        return detections


# ---------------------------------------------------------------------------
# Python model adapter (Pydantic / SQLAlchemy / dataclasses)
# ---------------------------------------------------------------------------

_PYDANTIC_BASES = {"BaseModel", "SQLModel"}
_SQLALCHEMY_BASES = {"Base", "DeclarativeBase", "DeclarativeMeta", "Model", "db.Model"}
_DATACLASS_DECS = {"dataclass"}
_MODEL_IMPORTS = [
    "pydantic",
    "sqlalchemy",
    "sqlmodel",
    "dataclasses",
    "flask_sqlalchemy",
    "peewee",
    "tortoise",
]


class DataClassificationPythonAdapter(FrameworkAdapter):
    """Detects PII/PHI fields in Pydantic, SQLAlchemy ORM, and ``@dataclass`` models."""

    name = "data_classification_py"
    priority = 5
    handles_imports = _MODEL_IMPORTS

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        detections: list[ComponentDetection] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # Check bases for Pydantic / SQLAlchemy parent classes
            base_names: set[str] = set()
            for b in node.bases:
                if isinstance(b, ast.Name):
                    base_names.add(b.id)
                elif isinstance(b, ast.Attribute):
                    base_names.add(b.attr)

            is_model = bool(base_names & (_PYDANTIC_BASES | _SQLALCHEMY_BASES))

            # Also accept @dataclass-decorated classes
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id in _DATACLASS_DECS:
                    is_model = True
                elif isinstance(dec, ast.Attribute) and dec.attr in _DATACLASS_DECS:
                    is_model = True

            if not is_model:
                continue

            # Collect field names from annotated assignments and plain assignments
            field_names: list[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.append(stmt.target.id)
                elif isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                            field_names.append(tgt.id)

            classified = classify_fields(field_names)
            if not classified:
                continue

            all_labels = sorted({lbl for lbls in classified.values() for lbl in lbls})

            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=f"datastore:model:{node.name.lower()}",
                    display_name=node.name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.95,
                    metadata={
                        "adapter": self.name,
                        "model_name": node.name,
                        "source": "python_model",
                        "data_classification": all_labels,
                        "classified_fields": classified,
                    },
                    file_path=file_path,
                    line=node.lineno,
                    snippet=f"class {node.name}",
                    evidence_kind="ast_instantiation",
                )
            )

        return detections
