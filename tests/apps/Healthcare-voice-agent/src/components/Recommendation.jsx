import { useLocation, useNavigate } from "react-router-dom";
import "./Recommendation.css";

// Backend URL from environment variable
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL;

export default function Recommendation() {
  const location = useLocation();
  const navigate = useNavigate();
  const {
    recommended_specialists = [],
    doctors = [],
    prognosis = "",
  } = location.state || {};

  const handlePayment = (doctor) => {
    const razorpayKey = import.meta.env.VITE_RAZORPAY_KEY;

    const bookAppointment = async () => {
      try {
        const userId = localStorage.getItem("user_id");

        const response = await fetch(`${BACKEND_URL}/appointments`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            patient_id: parseInt(userId),
            doctor_id: doctor.doctor_id,
            slot_id: doctor.slot_id,
            reason: "Booked via AI Assistant"
          }),
        });

        const data = await response.json();

        const patientDetails = await fetch(`${BACKEND_URL}/patient-details/${userId}`);
        const patientData = await patientDetails.json();

        const payload = {
          patientName: patientData.name,
          doctor: doctor.name,
          hospital: doctor.hospital,
          bookingId: `BOOK-${data.appointment_id}`,
          date: doctor.next_available_date,
          time: doctor.start_time
        };
        const encoded = encodeURIComponent(JSON.stringify(payload));
        navigate(`/success?data=${encoded}`);
      } catch (err) {
        console.error("Appointment creation failed:", err);
      }
    };

    if (!razorpayKey || !window.Razorpay) {
      // No payment key configured — skip payment gateway and book directly
      bookAppointment();
      return;
    }

    const options = {
      key: razorpayKey,
      amount: doctor.fees * 100,
      currency: "INR",
      name: "Healthcare Assistant",
      description: `Consultation with Dr. ${doctor.name}`,
      handler: async function () {
        await bookAppointment();
      },
      prefill: {
        name: "Amruta Hegde",
        email: "amruta@example.com",
        contact: "9999999999",
      },
      theme: {
        color: "#0d6efd",
      },
    };

    const rzp = new window.Razorpay(options);
    rzp.open();
  };

  function formatDateTime(dateString, timeString) {
    if (!dateString || !timeString) return "Not available";

    const date = new Date(`${dateString}T${timeString}`);
    const dayOfWeek = date.toLocaleDateString("en-GB", { weekday: "short" });
    const dateFormatted = date.toLocaleDateString("en-GB");
    const timeFormatted = date.toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
    });

    return `${dayOfWeek} ${dateFormatted} at ${timeFormatted}`;
  }

  return (
    <div className="recommendation-container">
      <h2 className="recommendation-heading">Consult Recommendation</h2>

      {prognosis && (
        <div className="prognosis-section" style={{ backgroundColor: '#f0f7ff', padding: '15px', borderRadius: '8px', marginBottom: '20px', borderLeft: '5px solid #007bff' }}>
          <h3 style={{ margin: '0 0 10px 0', color: '#0056b3' }}>🔍 Possible Prognosis</h3>
          <p style={{ fontStyle: 'italic', lineHeight: '1.5', color: '#333' }}>{prognosis}</p>
        </div>
      )}

      {recommended_specialists.length > 0 ? (
        <>
          <p className="recommendation-text">
            Based on your symptoms, we recommend consulting one of the following specialists:
          </p>
          <ul className="specialist-list">
            {recommended_specialists.map((spec, idx) => (
              <li key={idx}>{spec}</li>
            ))}
          </ul>
        </>
      ) : (
        <p className="warning-text">⚠️ No specialist recommendations available.</p>
      )}

      {doctors.length > 0 ? (
        <div className="doctor-card-container">
          {doctors.map((doc, index) => (
            <div className="doctor-card" key={index}>
              <div className="doctor-info">
                <p className="doctor-name">👨‍⚕️ {doc.name}</p>
                <p><strong>📘 Specialization:</strong> {doc.specialization}</p>
                <p><strong>🏥 Hospital:</strong> {doc.hospital}</p>
                <p><strong>⭐ Rating:</strong> {doc.rating} / 5</p>
                <p><strong>💰 Fee:</strong> ₹{doc.fees}</p>
                <p><strong>📅 Next Slot:</strong> {formatDateTime(doc.next_available_date, doc.start_time)}</p>
              </div>
              <div className="doctor-actions">
                <button className="book-btn" onClick={() => handlePayment(doc)}>
                  Book Appointment
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="warning-text">No doctors available for the selected specialists.</p>
      )}
    </div>
  );
}
