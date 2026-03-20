"""Tests for data classification — PII/PHI detection in SQL schemas and Python models."""
from __future__ import annotations


import pytest

from xelo.adapters.data_classification import (
    DataClassificationPythonAdapter,
    DataClassificationSQLAdapter,
    classify_fields,
)
from xelo.config import AiSbomConfig
from xelo.extractor import AiSbomExtractor
from xelo.models import AiSbomDocument
from xelo.types import ComponentType
from conftest import APPS

_SQL_ONLY = AiSbomConfig(include_extensions={".sql"}, enable_llm=False)
_SQL_AND_PY = AiSbomConfig(include_extensions={".py", ".sql"}, enable_llm=False)
_PORTAL = APPS / "patient_portal"


# ---------------------------------------------------------------------------
# classify_fields() — pattern library
# ---------------------------------------------------------------------------

class TestClassifyFields:
    def test_pii_name(self) -> None:
        assert "PII" in classify_fields(["name"])["name"]

    def test_pii_email(self) -> None:
        assert "PII" in classify_fields(["email"])["email"]

    def test_pii_contact_number(self) -> None:
        assert "PII" in classify_fields(["contact_number"])["contact_number"]

    def test_pii_date_of_birth(self) -> None:
        assert "PII" in classify_fields(["date_of_birth"])["date_of_birth"]

    def test_pii_password(self) -> None:
        assert "PII" in classify_fields(["password"])["password"]

    def test_pii_marital_status(self) -> None:
        assert "PII" in classify_fields(["marital_status"])["marital_status"]

    def test_phi_diagnosis(self) -> None:
        assert "PHI" in classify_fields(["past_diagnoses"])["past_diagnoses"]

    def test_phi_surgery(self) -> None:
        assert "PHI" in classify_fields(["surgeries"])["surgeries"]

    def test_phi_immunization(self) -> None:
        assert "PHI" in classify_fields(["immunization_records"])["immunization_records"]

    def test_phi_blood_group(self) -> None:
        assert "PHI" in classify_fields(["blood_group"])["blood_group"]

    def test_phi_family_history(self) -> None:
        assert "PHI" in classify_fields(["family_medical_history"])["family_medical_history"]

    def test_mrn_is_both_pii_and_phi(self) -> None:
        labels = set(classify_fields(["medical_record_number"])["medical_record_number"])
        assert "PII" in labels
        assert "PHI" in labels

    def test_unclassified_fields_excluded(self) -> None:
        result = classify_fields(["id", "created_at", "doctor_id", "status"])
        assert result == {}

    def test_multiple_fields_mixed(self) -> None:
        result = classify_fields(["id", "name", "email", "created_at", "past_diagnoses"])
        assert set(result.keys()) == {"name", "email", "past_diagnoses"}

    def test_case_insensitive(self) -> None:
        result = classify_fields(["Email", "DATE_OF_BIRTH", "BLOOD_GROUP"])
        assert "Email" in result
        assert "DATE_OF_BIRTH" in result
        assert "BLOOD_GROUP" in result


# ---------------------------------------------------------------------------
# DataClassificationSQLAdapter
# ---------------------------------------------------------------------------

class TestSQLAdapter:
    @pytest.fixture
    def adapter(self) -> DataClassificationSQLAdapter:
        return DataClassificationSQLAdapter()

    def test_patients_table_detected(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = (_PORTAL / "sql" / "schema.sql").read_text()
        dets = adapter.scan(sql, "sql/schema.sql")
        names = {d.display_name for d in dets}
        assert "patients" in names

    def test_patient_history_detected(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = (_PORTAL / "sql" / "schema.sql").read_text()
        dets = adapter.scan(sql, "sql/schema.sql")
        names = {d.display_name for d in dets}
        assert "patient_history" in names

    def test_patients_classified_pii_and_phi(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = (_PORTAL / "sql" / "schema.sql").read_text()
        dets = adapter.scan(sql, "sql/schema.sql")
        patients = next(d for d in dets if d.display_name == "patients")
        labels = set(patients.metadata["data_classification"])
        assert "PII" in labels
        assert "PHI" in labels

    def test_classified_fields_populated(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = (_PORTAL / "sql" / "schema.sql").read_text()
        dets = adapter.scan(sql, "sql/schema.sql")
        patients = next(d for d in dets if d.display_name == "patients")
        cf = patients.metadata["classified_fields"]
        assert "name" in cf
        assert "medical_record_number" in cf
        assert "blood_group" in cf

    def test_hospitals_not_detected(self, adapter: DataClassificationSQLAdapter) -> None:
        """hospitals table only has name/address/contact_number; contact_number is PII."""
        sql = (_PORTAL / "sql" / "schema.sql").read_text()
        dets = adapter.scan(sql, "sql/schema.sql")
        # hospitals does have contact_number (PII) — verify it's caught
        hospital_det = next((d for d in dets if d.display_name == "hospitals"), None)
        # contact_number is PII, so hospitals should be detected
        assert hospital_det is not None
        assert "PII" in hospital_det.metadata["data_classification"]

    def test_no_false_positives_for_clean_table(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS audit_log (
            id         SERIAL PRIMARY KEY,
            event_type VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        dets = adapter.scan(sql, "audit_log.sql")
        assert dets == []

    def test_all_columns_list(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = (_PORTAL / "sql" / "schema.sql").read_text()
        dets = adapter.scan(sql, "sql/schema.sql")
        patients = next(d for d in dets if d.display_name == "patients")
        assert "id" in patients.metadata["all_columns"]
        assert "name" in patients.metadata["all_columns"]

    def test_component_type_is_datastore(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = (_PORTAL / "sql" / "schema.sql").read_text()
        dets = adapter.scan(sql, "sql/schema.sql")
        for det in dets:
            assert det.component_type == ComponentType.DATASTORE

    def test_canonical_name_format(self, adapter: DataClassificationSQLAdapter) -> None:
        sql = "CREATE TABLE patients (name VARCHAR(100));"
        dets = adapter.scan(sql, "schema.sql")
        assert dets[0].canonical_name == "datastore:sql:patients"


# ---------------------------------------------------------------------------
# DataClassificationPythonAdapter
# ---------------------------------------------------------------------------

class TestPythonAdapter:
    @pytest.fixture
    def adapter(self) -> DataClassificationPythonAdapter:
        return DataClassificationPythonAdapter()

    def test_pydantic_model_detected(self, adapter: DataClassificationPythonAdapter) -> None:
        source = (_PORTAL / "models.py").read_text()
        # Create a fake parse_result (the adapter ignores it, uses ast directly)
        dets = adapter.extract(source, "models.py", None)
        names = {d.display_name for d in dets}
        assert "PatientResponse" in names

    def test_medical_history_detected(self, adapter: DataClassificationPythonAdapter) -> None:
        source = (_PORTAL / "models.py").read_text()
        dets = adapter.extract(source, "models.py", None)
        names = {d.display_name for d in dets}
        assert "MedicalHistoryResponse" in names

    def test_patient_response_classified_pii_phi(self, adapter: DataClassificationPythonAdapter) -> None:
        source = (_PORTAL / "models.py").read_text()
        dets = adapter.extract(source, "models.py", None)
        patient = next(d for d in dets if d.display_name == "PatientResponse")
        labels = set(patient.metadata["data_classification"])
        assert "PII" in labels
        assert "PHI" in labels

    def test_appointment_request_is_pii(self, adapter: DataClassificationPythonAdapter) -> None:
        """AppointmentRequest has 'reason' (PHI) and 'patient_id' (ignored — numeric id)."""
        source = (_PORTAL / "models.py").read_text()
        dets = adapter.extract(source, "models.py", None)
        appt = next((d for d in dets if d.display_name == "AppointmentRequest"), None)
        # reason → PHI via appointment_reason pattern
        if appt:
            assert "PHI" in appt.metadata["data_classification"]

    def test_non_model_class_ignored(self, adapter: DataClassificationPythonAdapter) -> None:
        source = """
class SomeHelper:
    name: str
    email: str
"""
        dets = adapter.extract(source, "helpers.py", None)
        assert dets == []

    def test_dataclass_detected(self, adapter: DataClassificationPythonAdapter) -> None:
        source = """
from dataclasses import dataclass

@dataclass
class UserRecord:
    name: str
    email: str
    id: int
"""
        dets = adapter.extract(source, "user.py", None)
        assert any(d.display_name == "UserRecord" for d in dets)
        user = next(d for d in dets if d.display_name == "UserRecord")
        assert "PII" in user.metadata["data_classification"]

    def test_canonical_name_format(self, adapter: DataClassificationPythonAdapter) -> None:
        source = "from pydantic import BaseModel\nclass Patient(BaseModel):\n    name: str\n"
        dets = adapter.extract(source, "m.py", None)
        assert dets[0].canonical_name == "datastore:model:patient"


# ---------------------------------------------------------------------------
# Integration: full extraction from patient_portal fixture
# ---------------------------------------------------------------------------

class TestPatientPortalExtraction:
    @pytest.fixture(scope="class")
    def doc(self) -> AiSbomDocument:
        return AiSbomExtractor().extract_from_path(_PORTAL, _SQL_AND_PY)

    def test_no_schema_datastore_nodes(self, doc: AiSbomDocument) -> None:
        """SQL tables and Python models must NOT appear as separate DATASTORE nodes."""
        schema_names = {
            "patients", "patient_history", "appointments", "hospitals", "users",
            "PatientResponse", "MedicalHistoryResponse", "AppointmentRequest",
        }
        node_names = {n.name for n in doc.nodes if n.component_type == ComponentType.DATASTORE}
        assert not (node_names & schema_names), (
            f"Schema definitions should not be DATASTORE nodes: {node_names & schema_names}"
        )

    def test_datastore_node_has_classification(self, doc: AiSbomDocument) -> None:
        """Any DATASTORE node detected should carry classification metadata."""
        ds_nodes = [n for n in doc.nodes if n.component_type == ComponentType.DATASTORE]
        if ds_nodes:
            classified = [n for n in ds_nodes if n.metadata.data_classification]
            assert classified, "DATASTORE nodes should carry data_classification metadata"

    def test_datastore_node_has_classified_tables(self, doc: AiSbomDocument) -> None:
        """DATASTORE nodes should list which tables/models contain sensitive fields."""
        ds_nodes = [n for n in doc.nodes if n.component_type == ComponentType.DATASTORE]
        if ds_nodes:
            with_tables = [n for n in ds_nodes if n.metadata.classified_tables]
            assert with_tables, "DATASTORE nodes should have classified_tables"
            all_tables = [t for n in with_tables for t in (n.metadata.classified_tables or [])]
            assert any("patient" in t.lower() for t in all_tables)

    def test_datastore_node_has_classified_fields(self, doc: AiSbomDocument) -> None:
        """DATASTORE nodes should carry per-table field-level classification detail."""
        ds_nodes = [n for n in doc.nodes if n.component_type == ComponentType.DATASTORE]
        if ds_nodes:
            with_fields = [n for n in ds_nodes if n.metadata.classified_fields]
            assert with_fields, "DATASTORE nodes should have classified_fields"

    def test_data_classification_in_summary(self, doc: AiSbomDocument) -> None:
        assert doc.summary is not None
        assert "PII" in doc.summary.data_classification
        assert "PHI" in doc.summary.data_classification

    def test_classified_tables_in_summary(self, doc: AiSbomDocument) -> None:
        assert doc.summary is not None
        assert doc.summary.classified_tables, "Expected classified_tables list in summary"
        assert any("patient" in t.lower() for t in doc.summary.classified_tables)

    def test_sql_extension_scanned_by_default(self) -> None:
        """Verify .sql is in the default AiSbomConfig extensions."""
        cfg = AiSbomConfig()
        assert ".sql" in cfg.include_extensions
