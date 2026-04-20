CREATE OR REPLACE FUNCTION sp_get_medical_history(p_patient_id INT)
RETURNS TABLE(
    past_diagnoses TEXT,
    surgeries TEXT,
    hospital_admissions TEXT,
    immunization_records TEXT,
    family_medical_history TEXT,
    lifestyle_factors TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        ph.past_diagnoses,
        ph.surgeries,
        ph.hospital_admissions,
        ph.immunization_records,
        ph.family_medical_history,
        ph.lifestyle_factors
    FROM patient_history ph
    WHERE ph.patient_id = p_patient_id;
END;
$$ LANGUAGE plpgsql;
