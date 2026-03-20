"""Pydantic models for the patient portal API."""
from pydantic import BaseModel
from typing import Optional


class PatientResponse(BaseModel):
    id: int
    name: str
    date_of_birth: str
    gender: str
    contact_number: str
    medical_record_number: str
    blood_group: str
    marital_status: str


class MedicalHistoryResponse(BaseModel):
    past_diagnoses: Optional[str]
    surgeries: Optional[str]
    hospital_admissions: Optional[str]
    immunization_records: Optional[str]
    family_medical_history: Optional[str]
    lifestyle_factors: Optional[str]


class AppointmentRequest(BaseModel):
    patient_id: int
    doctor_id: int
    reason: str
