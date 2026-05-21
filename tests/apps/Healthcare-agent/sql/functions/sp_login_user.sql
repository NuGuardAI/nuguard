CREATE OR REPLACE FUNCTION sp_login_user(p_email TEXT, p_password TEXT)
RETURNS TABLE(
    id INT,
    email VARCHAR,
    password VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT u.id, u.email, u.password
    FROM users u
    WHERE u.email = p_email AND u.password = p_password;
END;
$$ LANGUAGE plpgsql;