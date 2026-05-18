import uuid

def schedule_planting_service(customer_id: str, date: str, time_range: str, details: str) -> dict:
  """Schedules a planting service appointment.

  Args:
      customer_id: The ID of the customer.
      date:  The desired date (YYYY-MM-DD).
      time_range: The desired time range (e.g., "8-12").
      details: Any additional details (e.g., "Planting Petunias").

  Returns:
      A dictionary indicating the status of the scheduling. Example:
      {'status': 'success', 'appointment_id': '12345', 'date': '2024-07-29', 'time': '8:00 AM - 12:00 PM'}
  """

  start_time_str = time_range.split("-")[0]
  confirmation_time_str = f"{date} {start_time_str}:00"
  
  return {
      "status": "success",
      "appointment_id": str(uuid.uuid4()),
      "date": date,
      "time": time_range,
      "confirmation_time": confirmation_time_str,
  }