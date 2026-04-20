from typing import Any

def get_user_accounts() -> dict[str, Any]:
    """
    Retrieves a list of accounts associated with the authenticated user.

    Returns:
        dict[str, Any]: A dictionary containing a list of user accounts.
              Example: {"accounts": [{"account_id": "CHK-001", "type": "Checking", "nickname": "My Checking"}]}
              Example: {"error": True, "message": "User not authenticated."}
    """
    # MOCK: This mock returns a predefined list of accounts for a mock user.
    # In a real scenario, this would query a core banking system for the authenticated user's accounts.
    user_id = context.state.get("user_id", "mock_user_123") # Default for demo if not authenticated
    is_authenticated = context.state.get("is_authenticated", False)

    if not is_authenticated and user_id == "mock_user_123": # Allow mock_user_123 for unauthenticated testing
        return {"error": True, "message": "User not authenticated. Please log in to view accounts."}

    mock_accounts_data = {
        "user_123": [
            {"account_id": "CHK-001", "type": "Checking", "nickname": "My Checking"},
            {"account_id": "SAV-002", "type": "Savings", "nickname": "My Savings"}
        ],
        "user_456": [
            {"account_id": "CHK-003", "type": "Checking", "nickname": "Jane's Checking"}
        ]
    }

    accounts = mock_accounts_data.get(user_id, [])
    if accounts:
        return {"accounts": accounts}
    else:
        return {"error": True, "message": "No accounts found for this user."}