CREATE OR REPLACE FUNCTION sp_get_patient_id(p_user_id INT)
RETURNS TABLE(id INT) AS $$
BEGIN
    RETURN QUERY
    SELECT p.id FROM patients p WHERE p.user_id = p_user_id;
END;
$$ LANGUAGE plpgsql;