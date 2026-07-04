--------------- Database Schema ---------------

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patients (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    date_of_birth DATE NOT NULL,
    gender VARCHAR(10) NOT NULL CHECK (gender IN ('Male', 'Female', 'Other')),
    contact_number VARCHAR(15) UNIQUE NOT NULL,
    medical_record_number VARCHAR(20) UNIQUE NOT NULL,
    blood_group VARCHAR(10) CHECK (blood_group IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')),
    marital_status VARCHAR(20) CHECK (marital_status IN ('Single', 'Married', 'Divorced', 'Widowed', 'Other')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_history (
    id SERIAL PRIMARY KEY,
    patient_id INT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    past_diagnoses TEXT,
    surgeries TEXT,
    hospital_admissions TEXT,
    immunization_records TEXT,
    family_medical_history TEXT,
    lifestyle_factors TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hospitals (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    address TEXT,
    contact_number VARCHAR(15),
    website TEXT
);

CREATE TABLE IF NOT EXISTS specialists (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS doctors (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    specialist_id INT REFERENCES specialists(id) ON DELETE SET NULL,
    hospital_id INTEGER REFERENCES hospitals(id) ON DELETE SET NULL,
    specialization VARCHAR(100),
    experience INTEGER CHECK (experience >= 0),
    rating NUMERIC(2,1) CHECK (rating >= 0 AND rating <= 5),
    fees INT
);

CREATE TABLE IF NOT EXISTS availability_slots (
    id SERIAL PRIMARY KEY,
    doctor_id INT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    available_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (start_time < end_time)
);

CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    patient_id INT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id INT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    slot_id INT NOT NULL REFERENCES availability_slots(id) ON DELETE CASCADE,
    appointment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS symptoms (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS specialist_symptom (
    specialist_id INT REFERENCES specialists(id) ON DELETE CASCADE,
    symptom_id INT REFERENCES symptoms(id) ON DELETE CASCADE,
    PRIMARY KEY (specialist_id, symptom_id)
);

--------------- Seed Data ---------------

INSERT INTO users (email, password)
VALUES ('john@google.com', 'user2')
ON CONFLICT (email) DO NOTHING;

INSERT INTO patients (user_id, name, date_of_birth, gender, contact_number, medical_record_number, blood_group, marital_status)
VALUES (1, 'John Doe', '1990-01-01', 'Male', '1234567890', 'MRN12345', 'O+', 'Single')
ON CONFLICT (contact_number) DO NOTHING;

INSERT INTO patient_history (patient_id, past_diagnoses, surgeries, hospital_admissions, immunization_records, family_medical_history, lifestyle_factors)
VALUES (1, 'None', 'None', 'None', 'All basic vaccines', 'None', 'Non-smoker')
ON CONFLICT DO NOTHING;

INSERT INTO hospitals (name) VALUES ('City Hospital'), ('General Clinic'), ('Healthcare Center')
ON CONFLICT (name) DO NOTHING;

INSERT INTO specialists (name) VALUES 
('Cardiologist'), ('Dermatologist'), ('Neurologist'), ('Pediatrician'), ('Orthopedic Surgeon'), ('General Physician') 
ON CONFLICT (name) DO NOTHING;

INSERT INTO symptoms (name) VALUES 
('Chest Pain'), ('Shortness of Breath'), ('Heart Palpitations'), 
('Skin Rash'), ('Itching'), ('Acne'),
('Headache'), ('Dizziness'), ('Seizures'),
('Fever'), ('Cough'), ('Sore Throat'),
('Joint Pain'), ('Back Pain'), ('Fracture')
ON CONFLICT (name) DO NOTHING;

INSERT INTO specialist_symptom (specialist_id, symptom_id) 
SELECT s.id, sy.id FROM specialists s, symptoms sy 
WHERE (s.name='Cardiologist' AND sy.name IN ('Chest Pain', 'Shortness of Breath', 'Heart Palpitations'))
   OR (s.name='Dermatologist' AND sy.name IN ('Skin Rash', 'Itching', 'Acne'))
   OR (s.name='Neurologist' AND sy.name IN ('Headache', 'Dizziness', 'Seizures'))
   OR (s.name='General Physician' AND sy.name IN ('Fever', 'Cough', 'Sore Throat'))
   OR (s.name='Orthopedic Surgeon' AND sy.name IN ('Joint Pain', 'Back Pain', 'Fracture'))
ON CONFLICT DO NOTHING;

INSERT INTO doctors (name, specialist_id, hospital_id, specialization, rating, fees) 
SELECT 'Dr. Smith', id, 1, 'Senior Cardiologist', 4.8, 150 FROM specialists WHERE name='Cardiologist'
UNION ALL
SELECT 'Dr. Jones', id, 2, 'Dermatologist Specialist', 4.5, 100 FROM specialists WHERE name='Dermatologist'
UNION ALL
SELECT 'Dr. Lee', id, 3, 'Senior Neurologist', 4.9, 200 FROM specialists WHERE name='Neurologist'
UNION ALL
SELECT 'Dr. Brown', id, 1, 'General Physician', 4.2, 80 FROM specialists WHERE name='General Physician'
ON CONFLICT DO NOTHING;

INSERT INTO availability_slots (doctor_id, available_date, start_time, end_time)
SELECT id, CURRENT_DATE + INTERVAL '1 day', '09:00:00'::TIME, '10:00:00'::TIME FROM doctors WHERE name='Dr. Smith'
UNION ALL
SELECT id, CURRENT_DATE + INTERVAL '1 day', '10:00:00'::TIME, '11:00:00'::TIME FROM doctors WHERE name='Dr. Jones'
UNION ALL
SELECT id, CURRENT_DATE + INTERVAL '2 days', '11:00:00'::TIME, '12:00:00'::TIME FROM doctors WHERE name='Dr. Lee'
UNION ALL
SELECT id, CURRENT_DATE + INTERVAL '1 day', '08:00:00'::TIME, '09:00:00'::TIME FROM doctors WHERE name='Dr. Brown'
ON CONFLICT DO NOTHING;
