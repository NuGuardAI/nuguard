import datetime
from typing import Any, Optional

def authenticate_user(username: str, password: str) -> dict[str, Any]:
    """
    Authenticates a user with the provided username and password.

    Args:
        username (str): The user's login username.
        password (str): The user's login password.

    Returns:
        dict[str, Any]: A dictionary indicating authentication success, a message,
                        a token, and the user_id if successful.
              Example: {"success": True, "message": "Authentication successful.", "token": "abc123xyz", "user_id": "user_123"}
              Example: {"success": False, "message": "Invalid username or password."}
    """
    # MOCK: This mock simulates user authentication.
    # In a real scenario, this would call an external authentication service.
    mock_users = {
        "testuser": {"password": "testpass", "user_id": "user_123", "name": "John Doe"},
        "johndoe": {"password": "password123", "user_id": "user_456", "name": "Jane Smith"}
    }

    if username in mock_users and mock_users[username]["password"] == password:
        context.state["is_authenticated"] = True
        context.state["user_id"] = mock_users[username]["user_id"]
        context.state["username"] = username
        return {"success": True, "message": "Authentication successful.", "token": "mock_token_123", "user_id": mock_users[username]["user_id"]}
    else:
        context.state["is_authenticated"] = False
        return {"success": False, "message": "Invalid username or password."}