"""Test PII/PHI detection from SQL and Pydantic/SQLAlchemy model files."""

from __future__ import annotations

import pytest
from pathlib import Path


from nuguard.sbom.models import DataClassification
try:
    from nuguard.sbom.extractor.pii_classifier import PiiClassifier
except ImportError:
    pytest.skip("PiiClassifier not yet ported to nuguard.sbom", allow_module_level=True)


@pytest.fixture
def classifier() -> PiiClassifier:
    return PiiClassifier()


# ---------------------------------------------------------------------------
# SQL tests
# ---------------------------------------------------------------------------

SQL_WITH_PII = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255),
    name VARCHAR(100),
    phone VARCHAR(20)
);
"""

SQL_WITH_PHI = """
CREATE TABLE patient_records (
    id INTEGER PRIMARY KEY,
    patient_id INTEGER,
    diagnosis TEXT,
    medication TEXT
);
"""

SQL_WITHOUT_SENSITIVE = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    sku VARCHAR(50),
    price DECIMAL(10, 2),
    stock_count INTEGER
);
"""

SQL_MULTIPLE_TABLES = """
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    order_date DATE,
    total DECIMAL
);

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255),
    address TEXT
);
"""


def test_sql_pii_detection(classifier: PiiClassifier) -> None:
    """SQL CREATE TABLE with email/name/phone columns → PII."""
    results = classifier.classify_file(Path("schema.sql"), SQL_WITH_PII)
    table_names = {r[0] for r in results}
    classifications = {r[1] for r in results}
    assert "users" in table_names
    assert DataClassification.PII in classifications


def test_sql_phi_detection(classifier: PiiClassifier) -> None:
    """SQL CREATE TABLE with diagnosis/medication → PHI."""
    results = classifier.classify_file(Path("schema.sql"), SQL_WITH_PHI)
    classifications = {r[1] for r in results}
    assert DataClassification.PHI in classifications


def test_sql_no_classification(classifier: PiiClassifier) -> None:
    """A products table with non-sensitive columns returns no results."""
    results = classifier.classify_file(Path("schema.sql"), SQL_WITHOUT_SENSITIVE)
    assert results == []


def test_sql_multiple_tables(classifier: PiiClassifier) -> None:
    """Two tables in one file are classified independently."""
    results = classifier.classify_file(Path("schema.sql"), SQL_MULTIPLE_TABLES)
    table_names = {r[0] for r in results}
    # customers has email/address → PII; orders has no sensitive fields
    assert "customers" in table_names
    assert "orders" not in table_names


# ---------------------------------------------------------------------------
# Python (Pydantic / SQLAlchemy) tests
# ---------------------------------------------------------------------------

PYDANTIC_PII = """
from pydantic import BaseModel

class UserProfile(BaseModel):
    id: int
    email: str
    name: str
    phone: str | None = None
"""

PYDANTIC_PHI = """
from pydantic import BaseModel

class PatientRecord(BaseModel):
    patient_id: int
    diagnosis: str
    medication: str
"""

SQLALCHEMY_PII = """
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    email = Column(String)
    address = Column(String)
"""

PYDANTIC_NO_SENSITIVE = """
from pydantic import BaseModel

class ProductItem(BaseModel):
    sku: str
    price: float
    quantity: int
"""

NOT_A_MODEL = """
def calculate_discount(price: float, pct: float) -> float:
    return price * (1 - pct / 100)
"""


def test_pydantic_pii_detection(classifier: PiiClassifier) -> None:
    """Pydantic BaseModel with email/name/phone → PII."""
    results = classifier.classify_file(Path("models.py"), PYDANTIC_PII)
    assert any(r[0] == "UserProfile" and r[1] == DataClassification.PII for r in results)


def test_pydantic_phi_detection(classifier: PiiClassifier) -> None:
    """Pydantic BaseModel with diagnosis/medication → PHI."""
    results = classifier.classify_file(Path("models.py"), PYDANTIC_PHI)
    assert any(r[1] == DataClassification.PHI for r in results)


def test_sqlalchemy_pii_detection(classifier: PiiClassifier) -> None:
    """SQLAlchemy Base subclass with email/address → PII."""
    results = classifier.classify_file(Path("db/models.py"), SQLALCHEMY_PII)
    assert any(r[0] == "Customer" and r[1] == DataClassification.PII for r in results)


def test_pydantic_no_sensitive_fields(classifier: PiiClassifier) -> None:
    """A Pydantic model with no sensitive fields is not classified."""
    results = classifier.classify_file(Path("models.py"), PYDANTIC_NO_SENSITIVE)
    assert results == []


def test_non_model_python_file(classifier: PiiClassifier) -> None:
    """A Python file with no class definitions returns no results."""
    results = classifier.classify_file(Path("utils.py"), NOT_A_MODEL)
    assert results == []


def test_unsupported_extension(classifier: PiiClassifier) -> None:
    """A file with an unsupported extension returns no results."""
    results = classifier.classify_file(Path("config.yaml"), "email: foo@bar.com\n")
    assert results == []
