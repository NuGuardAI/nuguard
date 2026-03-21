"""PII/PHI classifier for SQL and Python model files.

Detects sensitive data in SQL CREATE TABLE statements and Python Pydantic/SQLAlchemy
model class definitions, classifying them as PII or PHI.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from nuguard.models.sbom import DataClassification

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII field name patterns (case-insensitive substring match)
# ---------------------------------------------------------------------------
_PII_PATTERNS = [
    "email",
    "name",
    "phone",
    "address",
    "birth",
    "gender",
    "ssn",
    "passport",
    "license",
    "credit_card",
    "bank_account",
    "ip_address",
    "mac_address",
    "device_id",
    "income",
    "salary",
    "wage",
]

# ---------------------------------------------------------------------------
# PHI field name patterns (case-insensitive substring match)
# ---------------------------------------------------------------------------
_PHI_PATTERNS = [
    "diagnosis",
    "prescription",
    "medication",
    "surgery",
    "immunization",
    "lab_result",
    "vital",
    "blood_type",
    "patient_id",
    "medical_record",
    "insurance",
    "mental_health",
    "allergy",
    "symptom",
    "icd_code",
    "provider_npi",
    "treatment",
]

# Regex for SQL CREATE TABLE parsing
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(([^;]*?)\)",
    re.IGNORECASE | re.DOTALL,
)
# Regex for column name in SQL (first identifier on each line of a CREATE TABLE body)
_COLUMN_NAME_RE = re.compile(r"^\s*(\w+)\s+", re.MULTILINE)


def _classify_fields(field_names: list[str]) -> DataClassification | None:
    """Classify a list of field names as PII, PHI, or None."""
    lower_names = [f.lower() for f in field_names]

    # PHI takes precedence
    for field in lower_names:
        for phi in _PHI_PATTERNS:
            if phi in field:
                return DataClassification.PHI

    for field in lower_names:
        for pii in _PII_PATTERNS:
            if pii in field:
                return DataClassification.PII

    return None


class PiiClassifier:
    """Classifies files for PII/PHI sensitive data patterns."""

    def classify_file(
        self, path: Path, source: str
    ) -> list[tuple[str, DataClassification]]:
        """Return list of (entity_name, DataClassification) tuples.

        Supports .sql and .py files. Returns [] for unsupported extensions.
        """
        suffix = path.suffix.lower()

        if suffix == ".sql":
            return self._classify_sql(source)
        elif suffix == ".py":
            return self._classify_python(source)
        else:
            return []

    def _classify_sql(self, source: str) -> list[tuple[str, DataClassification]]:
        """Parse SQL CREATE TABLE statements and classify each table."""
        results: list[tuple[str, DataClassification]] = []
        seen: set[str] = set()

        for match in _CREATE_TABLE_RE.finditer(source):
            table_name = match.group(1)
            body = match.group(2)

            if table_name in seen:
                continue
            seen.add(table_name)

            # Extract column names from the table body
            column_names = []
            for col_match in _COLUMN_NAME_RE.finditer(body):
                col = col_match.group(1)
                # Skip SQL keywords that appear at line start
                if col.upper() in (
                    "PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "KEY",
                    "INDEX", "CONSTRAINT", "NOT", "NULL", "DEFAULT",
                ):
                    continue
                column_names.append(col)

            classification = _classify_fields(column_names)
            if classification is not None:
                results.append((table_name, classification))

        return results

    def _classify_python(self, source: str) -> list[tuple[str, DataClassification]]:
        """Parse Python source and classify Pydantic/SQLAlchemy model classes."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            logger.debug("PiiClassifier: syntax error in Python file")
            return []

        results: list[tuple[str, DataClassification]] = []
        seen: set[str] = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            class_name = node.name
            if class_name in seen:
                continue

            if not _is_model_class(node):
                continue

            seen.add(class_name)
            field_names = _extract_python_fields(node)

            classification = _classify_fields(field_names)
            if classification is not None:
                results.append((class_name, classification))

        return results


def _is_model_class(node: ast.ClassDef) -> bool:
    """Return True if the class is a Pydantic BaseModel or SQLAlchemy ORM subclass."""
    for base in node.bases:
        base_name = _get_base_name(base)
        if base_name in (
            "BaseModel",        # Pydantic
            "Base",             # SQLAlchemy declarative Base
            "DeclarativeBase",  # SQLAlchemy modern
            "Model",            # Flask-SQLAlchemy / Django
            "db.Model",
        ):
            return True
    return False


def _get_base_name(base: ast.expr) -> str:
    """Get the name string from a base class expression."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return f"{_get_base_name(base.value)}.{base.attr}"
    return ""


def _extract_python_fields(node: ast.ClassDef) -> list[str]:
    """Extract field names from a class definition.

    Handles:
    - Pydantic: annotated assignments (field: type = ...)
    - SQLAlchemy: assignments like id = Column(...)
    - Dataclasses: annotated assignments
    """
    field_names: list[str] = []

    for stmt in node.body:
        # Annotated assignment: field: Type = default  (Pydantic style)
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                if not name.startswith("_") and name != "__tablename__":
                    field_names.append(name)

        # Regular assignment: field = Column(...)  (SQLAlchemy style)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    if not name.startswith("_") and name != "__tablename__":
                        field_names.append(name)

    return field_names
