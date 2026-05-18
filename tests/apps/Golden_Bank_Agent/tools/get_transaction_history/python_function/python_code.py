import datetime
from typing import Any, Optional

def get_transaction_history(
    account_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None
) -> dict[str, Any]:
    """
    Retrieves historical transaction data for a specified account, with optional filtering.

    Args:
        account_id (str): The unique identifier of the account.
        start_date (Optional[str]): The start date for the transaction history in YYYY-MM-DD format.
        end_date (Optional[str]): The end date for the transaction history in YYYY-MM-DD format.
        category (Optional[str]): An optional category to filter transactions (e.g., "Groceries", "Income").

    Returns:
        dict[str, Any]: A dictionary containing a list of transactions.
              Example: {"transactions": [{"id": "T001", "date": "2023-10-25", "description": "Grocery Store", "amount": -75.20}]}
              Example: {"error": True, "message": "Invalid date format."}
    """
    # MOCK: This mock provides sample transaction data and filters it.
    # In a real scenario, this would query a transaction database.
    user_id = context.state.get("user_id", "mock_user_123")
    is_authenticated = context.state.get("is_authenticated", False)
    current_date_str = context.state.get("current_date", datetime.date.today().isoformat())

    if not is_authenticated and user_id == "mock_user_123":
        return {"error": True, "message": "User not authenticated. Please log in to view transaction history."}

    mock_transactions_data = {
        "CHK-001": [
            {"id": "T001", "date": "2023-10-25", "description": "Grocery Store", "amount": -75.20, "category": "Groceries"},
            {"id": "T002", "date": "2023-10-20", "description": "Salary Deposit", "amount": 1500.00, "category": "Income"},
            {"id": "T003", "date": "2023-09-15", "description": "Online Shopping", "amount": -120.00, "category": "Shopping"},
            {"id": "T004", "date": "2023-08-01", "description": "Rent Payment", "amount": -1200.00, "category": "Housing"}
        ],
        "SAV-002": [
            {"id": "T005", "date": "2023-10-01", "description": "Interest Earned", "amount": 5.00, "category": "Income"}
        ]
    }

    transactions = mock_transactions_data.get(account_id, [])
    filtered_transactions = []

    try:
        parsed_start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
        parsed_end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else datetime.datetime.strptime(current_date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"error": True, "message": "Invalid date format. Please use YYYY-MM-DD."}

    for t in transactions:
        transaction_date = datetime.datetime.strptime(t["date"], "%Y-%m-%d").date()
        if (parsed_start_date is None or transaction_date >= parsed_start_date) and \
           (parsed_end_date is None or transaction_date <= parsed_end_date) and \
           (category is None or t["category"].lower() == category.lower()):
            filtered_transactions.append(t)

    if not filtered_transactions:
        return {"transactions": [], "message": "No transactions found matching your criteria."}
    return {"transactions": filtered_transactions}