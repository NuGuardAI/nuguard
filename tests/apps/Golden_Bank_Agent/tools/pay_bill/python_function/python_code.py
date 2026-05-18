from typing import Any

def pay_bill(account_id: str, biller_id: str, amount: float) -> dict[str, Any]:
    """
    Processes a payment to an existing biller from a specified account.

    Args:
        account_id (str): The ID of the account from which to make the payment.
        biller_id (str): The ID of the biller to pay.
        amount (float): The amount of money to pay.

    Returns:
        dict[str, Any]: A dictionary indicating the success or failure of the bill payment.
              Example: {"success": True, "message": "Bill paid successfully.", "confirmation_id": "BP-67890"}
              Example: {"success": False, "message": "Biller not found or invalid amount."}
    """
    # MOCK: This mock simulates a bill payment.
    # In a real scenario, this would interact with a bill payment service.
    user_id = context.state.get("user_id", "mock_user_123")
    is_authenticated = context.state.get("is_authenticated", False)

    if not is_authenticated and user_id == "mock_user_123":
        return {"success": False, "message": "User not authenticated. Please log in to pay bills."}

    # Simple mock validation
    if amount <= 0:
        return {"success": False, "message": "Payment amount must be positive."}

    mock_billers_data = {
        "user_123": [
            {"biller_id": "B001", "name": "Electricity Co.", "account_number": "123456789"},
            {"biller_id": "B002", "name": "Internet Provider", "account_number": "987654321"}
        ]
    }
    
    # Check if biller exists for the user
    user_billers = mock_billers_data.get(user_id, [])
    biller_found = any(b['biller_id'] == biller_id for b in user_billers)

    if not biller_found:
        return {"success": False, "message": f"Biller with ID '{biller_id}' not found for your profile."}

    # Mock account balances (simplified, not actually updated)
    mock_balances = {
        "CHK-001": 1500.75,
        "SAV-002": 5000.00,
    }

    if account_id not in mock_balances:
        return {"success": False, "message": f"Account '{account_id}' not found or unauthorized."}

    if mock_balances[account_id] < amount:
        return {"success": False, "message": f"Insufficient funds in account {account_id} to pay bill."}

    # Simulate success
    return {"success": True, "message": "Bill paid successfully.", "confirmation_id": f"BP-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"}