import { useEffect, useState } from "react";
import "./Success.css";

export default function Success() {
  const [data, setData] = useState({
    patientName: "",
    doctor: "",
    hospital: "",
    bookingId: "",
  });

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const queryData = JSON.parse(params.get("data"));
    setData(queryData);
  }, []);

  return (
    <div className="success-container">
      <h2 className="success-title">✔ Appointment Confirmed</h2>
      <p className="success-message">Your appointment has been successfully booked!</p>

      <div className="summary-box">
        <div className="summary-column">
          <h3>Appointment Summary</h3>
          <p><strong>Patient Name:</strong> {data.patientName}</p>
          <p><strong>Doctor:</strong> {data.doctor}</p>
          <p><strong>Hospital/Clinic:</strong> {data.hospital}</p>
          <p><strong>Booking ID:</strong> {data.bookingId}</p>
        </div>

        <div className="summary-column">
          <h3>Additional Information</h3>
          <p><strong>Check-in Time:</strong> Tomorrow, 9:45 AM</p>
          <p><strong>Required Document:</strong> Valid ID Proof</p>
          <p><strong>Support Contact:</strong> 1800-123-456</p>
        </div>
      </div>

      <div className="action-buttons">
        <button>Download Appointment Slip</button>
        <button>Add to Calendar</button>
        <button>Reschedule</button>
      </div>
    </div>
  );
}
