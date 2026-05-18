from pydantic import BaseModel
from typing import Optional

class LoginRequest(BaseModel):
    email: str
    password: str

class AppointmentRequest(BaseModel):
    patient_id: int
    doctor_id: int
    slot_id: int
    reason: str

class PatientDetailsResponse(BaseModel):
    name: str
    date_of_birth: str
    gender: str
    contact_number: str
    medical_record_number: str
    blood_group: str
    marital_status: str
    id: int

class MedicalHistoryResponse(BaseModel):
    past_diagnoses: Optional[str]
    surgeries: Optional[str]
    hospital_admissions: Optional[str]
    immunization_records: Optional[str]
    family_medical_history: Optional[str]
    lifestyle_factors: Optional[str]