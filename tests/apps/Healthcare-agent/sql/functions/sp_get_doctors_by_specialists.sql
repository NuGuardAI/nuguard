CREATE OR REPLACE FUNCTION sp_get_doctors_by_specialists(specialists TEXT[])
RETURNS TABLE (
    doctor_id INT,
    name TEXT,
    specialization TEXT,
    rating NUMERIC,
    fees INT,
    hospital TEXT,
    next_available_date DATE,
    start_time TIME,
    end_time TIME,
    slot_id INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        d.id,
        d.name::TEXT,
        d.specialization::TEXT,
        d.rating,
        d.fees,
        h.name::TEXT,
        a.available_date,
        a.start_time,
        a.end_time,
        a.id
    FROM doctors d
    JOIN hospitals h ON d.hospital_id = h.id
    LEFT JOIN availability_slots a ON d.id = a.doctor_id
    JOIN specialists s ON d.specialist_id = s.id
    WHERE s.name = ANY(specialists)
    ORDER BY a.available_date, a.start_time;
END;
$$ LANGUAGE plpgsql;
