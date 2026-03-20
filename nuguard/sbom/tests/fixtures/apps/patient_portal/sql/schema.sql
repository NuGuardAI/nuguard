-- Patient Portal schema — used as a Xelo data-classification test fixture.

CREATE TABLE IF NOT EXISTS users (
    id           SERIAL PRIMARY KEY,
    email        VARCHAR(255) UNIQUE NOT NULL,
    password     VARCHAR(255) NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patients (
    id                    SERIAL PRIMARY KEY,
    user_id               INT NOT NULL REFERENCES users(id),
    name                  VARCHAR(100) NOT NULL,
    date_of_birth         DATE NOT NULL,
    gender                VARCHAR(10),
    contact_number        VARCHAR(15) UNIQUE NOT NULL,
    medical_record_number VARCHAR(20) UNIQUE NOT NULL,
    blood_group           VARCHAR(10),
    marital_status        VARCHAR(20),
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_history (
    id                    SERIAL PRIMARY KEY,
    patient_id            INT NOT NULL REFERENCES patients(id),
    past_diagnoses        TEXT,
    surgeries             TEXT,
    hospital_admissions   TEXT,
    immunization_records  TEXT,
    family_medical_history TEXT,
    lifestyle_factors     TEXT,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS appointments (
    id               SERIAL PRIMARY KEY,
    patient_id       INT NOT NULL REFERENCES patients(id),
    doctor_id        INT NOT NULL,
    appointment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason           VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS hospitals (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(100) UNIQUE NOT NULL,
    address        TEXT,
    contact_number VARCHAR(15)
);
