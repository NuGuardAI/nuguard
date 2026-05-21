CREATE OR REPLACE FUNCTION sp_get_patient_details(p_user_id INT)
RETURNS TABLE(
    name TEXT,
    date_of_birth DATE,
    gender TEXT,
    contact_number TEXT,
    medical_record_number TEXT,
    blood_group TEXT,
    marital_status TEXT,
    id INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        p.name::TEXT,
        p.date_of_birth,
        p.gender::TEXT,
        p.contact_number::TEXT,
        p.medical_record_number::TEXT,
        p.blood_group::TEXT,
        p.marital_status::TEXT,
        p.id
    FROM patients p
    WHERE p.user_id = p_user_id;
END;
$$ LANGUAGE plpgsql;
