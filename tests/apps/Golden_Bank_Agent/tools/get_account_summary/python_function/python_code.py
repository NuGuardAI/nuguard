from typing import Any

def get_account_summary(account_id: str) -> dict[str, Any]:
    """
    Retrieves the summary details for a specific account, including balance, limit, and status.

    Args:
        account_id (str): The unique identifier of the account.

    Returns:
        dict[str, Any]: A dictionary containing the account summary.
              Example: {"balance": 1500.75, "limit": 5000.00, "status": "Active"}
              Example: {"error": True, "message": "Account not found or unauthorized."}
    """
    # MOCK: This mock provides a summary for predefined accounts.
    # In a real scenario, this would fetch real-time data from a core banking system.
    user_id = context.state.get("user_id", "mock_user_123")
    is_authenticated = context.state.get("is_authenticated", False)

    if not is_authenticated and user_id == "mock_user_123":
        return {"error": True, "message": "User not authenticated. Please log in to view account summary."}

    mock_full_account_data = {
        "user_123": {
            "CHK-001": {"balance": 1500.75, "limit": None, "status": "Active"},
            "SAV-002": {"balance": 5000.00, "limit": None, "status": "Active"}
        },
        "user_456": {
            "CHK-003": {"balance": 250.50, "limit": None, "status": "Active"}
        }
    }

    user_accounts = mock_full_account_data.get(user_id, {})
    summary = user_accounts.get(account_id)

    if summary:
        return summary
    else:
        return {"error": True, "message": f"Account '{account_id}' not found or unauthorized for user '{user_id}'."}