
-- Additional Specialists
INSERT INTO specialists (name) VALUES 
('Family Physician'), 
('Psychiatrist'), 
('Internal Medicine Specialist'), 
('Gastroenterologist'), 
('General Surgeon')
ON CONFLICT (name) DO NOTHING;

-- Additional Symptoms
INSERT INTO symptoms (name) VALUES 
('Abdominal Pain'), ('Nausea'), ('Bloating'), ('Heartburn'), -- Gastro
('Anxiety'), ('Depression'), ('Insomnia'), ('Mood Swings'), -- Psychiatrist
('Chronic Fatigue'), ('Weight Loss'), ('High Blood Pressure'), -- Internal Medicine / Family
('General Weakness'), ('Mild Fever'), ('Head Cold'), -- Family Physician
('Lump'), ('Swelling'), ('Wound that wont heal') -- General Surgeon
ON CONFLICT (name) DO NOTHING;

-- Map Symptoms to Specialists
INSERT INTO specialist_symptom (specialist_id, symptom_id) 
SELECT s.id, sy.id FROM specialists s, symptoms sy 
WHERE (s.name='Gastroenterologist' AND sy.name IN ('Abdominal Pain', 'Nausea', 'Bloating', 'Heartburn'))
   OR (s.name='Psychiatrist' AND sy.name IN ('Anxiety', 'Depression', 'Insomnia', 'Mood Swings'))
   OR (s.name='Internal Medicine Specialist' AND sy.name IN ('Chronic Fatigue', 'Weight Loss', 'High Blood Pressure'))
   OR (s.name='Family Physician' AND sy.name IN ('General Weakness', 'Mild Fever', 'Head Cold', 'Fever', 'Cough'))
   OR (s.name='General Surgeon' AND sy.name IN ('Lump', 'Swelling', 'Wound that wont heal'))
ON CONFLICT DO NOTHING;

-- Add Doctors for these specialists
INSERT INTO doctors (name, specialist_id, hospital_id, specialization, rating, fees) 
SELECT 'Dr. Garcia', id, 1, 'Family Medicine', 4.7, 70 FROM specialists WHERE name='Family Physician'
AND NOT EXISTS (SELECT 1 FROM doctors WHERE name='Dr. Garcia')
UNION ALL
SELECT 'Dr. Miller', id, 2, 'Psychiatrist', 4.6, 180 FROM specialists WHERE name='Psychiatrist'
AND NOT EXISTS (SELECT 1 FROM doctors WHERE name='Dr. Miller')
UNION ALL
SELECT 'Dr. Wilson', id, 3, 'Gastroenterology', 4.8, 160 FROM specialists WHERE name='Gastroenterologist'
AND NOT EXISTS (SELECT 1 FROM doctors WHERE name='Dr. Wilson')
UNION ALL
SELECT 'Dr. Moore', id, 1, 'Internal Medicine', 4.5, 120 FROM specialists WHERE name='Internal Medicine Specialist'
AND NOT EXISTS (SELECT 1 FROM doctors WHERE name='Dr. Moore')
UNION ALL
SELECT 'Dr. Taylor', id, 2, 'General Surgeon', 4.9, 250 FROM specialists WHERE name='General Surgeon'
AND NOT EXISTS (SELECT 1 FROM doctors WHERE name='Dr. Taylor');

-- Add Availability Slots
INSERT INTO availability_slots (doctor_id, available_date, start_time, end_time)
SELECT id, CURRENT_DATE + INTERVAL '1 day', '09:30:00'::TIME, '10:30:00'::TIME FROM doctors WHERE name='Dr. Garcia'
UNION ALL
SELECT id, CURRENT_DATE + INTERVAL '1 day', '14:00:00'::TIME, '15:00:00'::TIME FROM doctors WHERE name='Dr. Miller'
UNION ALL
SELECT id, CURRENT_DATE + INTERVAL '2 days', '10:00:00'::TIME, '11:00:00'::TIME FROM doctors WHERE name='Dr. Wilson'
UNION ALL
SELECT id, CURRENT_DATE + INTERVAL '1 day', '11:00:00'::TIME, '12:00:00'::TIME FROM doctors WHERE name='Dr. Moore'
UNION ALL
SELECT id, CURRENT_DATE + INTERVAL '3 days', '08:00:00'::TIME, '09:00:00'::TIME FROM doctors WHERE name='Dr. Taylor'
ON CONFLICT DO NOTHING;
