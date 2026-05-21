CREATE OR REPLACE FUNCTION sp_create_appointment(
    p_patient_id INT,
    p_doctor_id INT,
    p_slot_id INT,
    p_reason TEXT
)
RETURNS TABLE(appointment_id INT) AS $$
BEGIN
    RETURN QUERY
    INSERT INTO appointments (patient_id, doctor_id, slot_id, reason)
    VALUES (p_patient_id, p_doctor_id, p_slot_id, p_reason)
    RETURNING id;
END;
$$ LANGUAGE plpgsql;
