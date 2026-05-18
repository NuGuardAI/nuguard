import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

// Read the backend URL from environment variable
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL;

const PatientDashboard = () => {
  const [patient, setPatient] = useState(null);
  const [history, setHistory] = useState(null);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  const userId = localStorage.getItem("user_id");

  useEffect(() => {
    if (!userId) {
      navigate("/login");
      return;
    }

    // Fetch patient details
    fetch(`${BACKEND_URL}/patient-details/${userId}`)
      .then(res => {
        if (!res.ok) throw new Error("Failed to fetch patient details");
        return res.json();
      })
      .then(data => setPatient(data))
      .catch(err => setError(err.message));

    // Fetch medical history
    fetch(`${BACKEND_URL}/medical-history/${userId}`)
      .then(res => {
        if (!res.ok) throw new Error("Failed to fetch medical history");
        return res.json();
      })
      .then(data => setHistory(data))
      .catch(err => setError(err.message));
  }, [navigate, userId]);

  if (error) return <div className="error">Error: {error}</div>;
  if (!patient || !history) return <div>Loading...</div>;

  return (
    <div className="dashboard-container">
      <h2>Welcome, {patient.name}</h2>
      <p><strong>Date of Birth:</strong> {patient.date_of_birth}</p>
      <p><strong>Gender:</strong> {patient.gender}</p>
      <p><strong>Contact Number:</strong> {patient.contact_number}</p>
      <p><strong>Medical Record Number:</strong> {patient.medical_record_number}</p>
      <p><strong>Blood Group:</strong> {patient.blood_group}</p>
      <p><strong>Marital Status:</strong> {patient.marital_status}</p>

      <div className="dashboard-actions" style={{ marginTop: '20px', marginBottom: '20px' }}>
          <button 
            onClick={() => navigate("/assistant")}
            style={{ 
                padding: '10px 20px', 
                backgroundColor: '#007bff', 
                color: 'white', 
                border: 'none', 
                borderRadius: '5px', 
                cursor: 'pointer',
                fontSize: '16px',
                fontWeight: 'bold'
            }}
          >
            🎙️ Talk to AI Assistant
          </button>
      </div>

      <h3>Medical History</h3>
      <p><strong>Past Diagnoses:</strong> {history.past_diagnoses}</p>
      <p><strong>Surgeries:</strong> {history.surgeries}</p>
      <p><strong>Hospital Admissions:</strong> {history.hospital_admissions}</p>
      <p><strong>Immunization Records:</strong> {history.immunization_records}</p>
      <p><strong>Family Medical History:</strong> {history.family_medical_history}</p>
      <p><strong>Lifestyle Factors:</strong> {history.lifestyle_factors}</p>
    </div>
  );
};

export default PatientDashboard;
