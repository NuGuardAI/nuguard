from typing import Any

def transfer_funds(from_account_id: str, to_account_id: str, amount: float) -> dict[str, Any]:
    """
    Transfers funds securely between two internal Golden Bank accounts.

    Args:
        from_account_id (str): The ID of the account to transfer funds from.
        to_account_id (str): The ID of the account to transfer funds to.
        amount (float): The amount of money to transfer.

    Returns:
        dict[str, Any]: A dictionary indicating the success or failure of the transfer.
              Example: {"success": True, "message": "Funds transferred successfully.", "transaction_id": "TRN-12345"}
              Example: {"success": False, "message": "Insufficient funds in source account."}
    """
    # MOCK: This mock simulates a fund transfer.
    # In a real scenario, this would interact with a core banking transaction API.
    user_id = context.state.get("user_id", "mock_user_123")
    is_authenticated = context.state.get("is_authenticated", False)

    if not is_authenticated and user_id == "mock_user_123":
        return {"success": False, "message": "User not authenticated. Please log in to transfer funds."}

    # Simple mock validation
    if amount <= 0:
        return {"success": False, "message": "Transfer amount must be positive."}
    if from_account_id == to_account_id:
        return {"success": False, "message": "Cannot transfer funds to the same account."}

    # Mock account balances (simplified, not actually updated)
    mock_balances = {
        "CHK-001": 1500.75,
        "SAV-002": 5000.00,
        "CHK-003": 250.50
    }

    if from_account_id not in mock_balances or to_account_id not in mock_balances:
        return {"success": False, "message": "One or both account IDs are invalid."}

    if mock_balances[from_account_id] < amount:
        return {"success": False, "message": f"Insufficient funds in account {from_account_id}."}

    # Simulate success
    return {"success": True, "message": "Funds transferred successfully.", "transaction_id": f"TRN-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"}