"""PII/PHI classifier for SQL and Python ORM/Pydantic model files.

The classifier inspects:
- SQL files: ``CREATE TABLE`` statements → extract column names → match patterns
- Python files: ``class Foo(BaseModel)`` or ``class Foo(Base)`` (SQLAlchemy) →
  extract field names → match patterns

Returns a list of ``(table_or_model_name, DataClassification)`` tuples.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from nuguard.models.sbom import DataClassification

_PII_PATTERNS: list[str] = [
    r"\bname\b",
    r"\bemail\b",
    r"\bphone\b",
    r"\bssn\b",
    r"\bsocial_security\b",
    r"\bdob\b",
    r"\bdate_of_birth\b",
    r"\bbirthdate\b",
    r"\baddress\b",
    r"\bstreet\b",
    r"\bzip\b",
    r"\bpostcode\b",
    r"\bpassport\b",
    r"\bdriver_license\b",
    r"\bnational_id\b",
    r"\bip_address\b",
    r"\buser_id\b",
    r"\busername\b",
    r"\bgender\b",
    r"\bethnicity\b",
    r"\brace\b",
]

_PHI_PATTERNS: list[str] = [
    r"\bdiagnosis\b",
    r"\bmedication\b",
    r"\bprescription\b",
    r"\bmrn\b",
    r"\bmedical_record\b",
    r"\blab_result\b",
    r"\btest_result\b",
    r"\bhealth\b",
    r"\bpatient\b",
    r"\btreatment\b",
    r"\bcondition\b",
    r"\ballergy\b",
    r"\bvaccine\b",
    r"\bprocedure\b",
    r"\bprovider\b",
    r"\bclinic\b",
    r"\bhospital\b",
    r"\binsurance\b",
    r"\bblood_type\b",
    r"\bweight\b",
    r"\bheight\b",
]

_PII_RE = re.compile("|".join(_PII_PATTERNS), re.IGNORECASE)
_PHI_RE = re.compile("|".join(_PHI_PATTERNS), re.IGNORECASE)

# Match CREATE TABLE statements
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:[`\"\[]?)(\w+)(?:[`\"\]]?)\s*\(([^;]+)\)",
    re.IGNORECASE | re.DOTALL,
)
# Match column definitions inside CREATE TABLE body
_COLUMN_RE = re.compile(r"^\s*[`\"\[]?(\w+)[`\"\]]?\s+\w+", re.MULTILINE)

_BASE_MODEL_NAMES = frozenset({"BaseModel", "Base", "SQLModel"})


class PiiClassifier:
    """Classify tables/models by PII or PHI content."""

    # Expose compiled patterns as class attributes for test introspection
    PII_PATTERNS: list[str] = _PII_PATTERNS
    PHI_PATTERNS: list[str] = _PHI_PATTERNS

    def classify_file(
        self, file_path: Path, source: str
    ) -> list[tuple[str, DataClassification]]:
        """Return ``(name, classification)`` pairs for tables/models in *file*.

        A model/table receives the highest applicable classification:
        PHI > PII > nothing.

        Args:
            file_path: Path to the file (used to select SQL vs Python strategy).
            source: Full text of the file.

        Returns:
            List of ``(table_or_model_name, DataClassification)`` pairs.
        """
        suffix = file_path.suffix.lower()
        if suffix == ".sql":
            return self._classify_sql(source)
        elif suffix == ".py":
            return self._classify_python(source)
        return []

    # ------------------------------------------------------------------
    # SQL strategy
    # ------------------------------------------------------------------

    def _classify_sql(self, source: str) -> list[tuple[str, DataClassification]]:
        results: list[tuple[str, DataClassification]] = []
        for match in _CREATE_TABLE_RE.finditer(source):
            table_name = match.group(1)
            body = match.group(2)
            columns = [m.group(1) for m in _COLUMN_RE.finditer(body)]
            fields_text = " ".join(columns)
            classification = self._classify_fields(fields_text)
            if classification is not None:
                results.append((table_name, classification))
        return results

    # ------------------------------------------------------------------
    # Python strategy
    # ------------------------------------------------------------------

    def _classify_python(self, source: str) -> list[tuple[str, DataClassification]]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        results: list[tuple[str, DataClassification]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not self._is_orm_or_pydantic(node):
                continue
            field_names = self._extract_class_fields(node)
            fields_text = " ".join(field_names)
            classification = self._classify_fields(fields_text)
            if classification is not None:
                results.append((node.name, classification))
        return results

    @staticmethod
    def _is_orm_or_pydantic(node: ast.ClassDef) -> bool:
        """Return True when the class inherits from a known ORM/model base."""
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in _BASE_MODEL_NAMES:
                return True
            if isinstance(base, ast.Attribute) and base.attr in _BASE_MODEL_NAMES:
                return True
        return False

    @staticmethod
    def _extract_class_fields(node: ast.ClassDef) -> list[str]:
        """Return field names from a Pydantic/SQLAlchemy class body."""
        fields: list[str] = []
        for item in node.body:
            # Pydantic: field_name: Type = ...
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.append(item.target.id)
            # SQLAlchemy Column assignment: field_name = Column(...)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        fields.append(target.id)
        return fields

    @staticmethod
    def _classify_fields(fields_text: str) -> DataClassification | None:
        """Apply PII/PHI regex patterns to a space-joined field name string."""
        if _PHI_RE.search(fields_text):
            return DataClassification.PHI
        if _PII_RE.search(fields_text):
            return DataClassification.PII
        return None
