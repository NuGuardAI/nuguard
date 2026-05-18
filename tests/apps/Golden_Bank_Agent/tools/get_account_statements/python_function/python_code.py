import datetime
from typing import Any, Optional

def get_account_statements(account_id: str, year: Optional[int] = None) -> dict[str, Any]:
    """
    Retrieves a list of available monthly account statements for a given account and year.

    Args:
        account_id (str): The unique identifier of the account.
        year (Optional[int]): The year for which to retrieve statements. Defaults to the current year.

    Returns:
        dict[str, Any]: A dictionary containing a list of statements with their month, year, and a download URL.
              Example: {"statements": [{"month": "October", "year": 2023, "url": "https://mockbank.com/statements/CHK-001-2023-10.pdf"}]}
              Example: {"error": True, "message": "No statements found for the specified year."}
    """
    # MOCK: This mock provides sample statement URLs.
    # In a real scenario, this would link to a document management system.
    user_id = context.state.get("user_id", "mock_user_123")
    is_authenticated = context.state.get("is_authenticated", False)
    current_date_str = context.state.get("current_date", datetime.date.today().isoformat())

    if not is_authenticated and user_id == "mock_user_123":
        return {"error": True, "message": "User not authenticated. Please log in to view statements."}

    current_year = datetime.datetime.strptime(current_date_str, "%Y-%m-%d").year
    target_year = year if year is not None else current_year

    mock_statements_data = {
        "CHK-001": {
            2023: [
                {"month": "January", "url": f"https://mockbank.com/statements/{account_id}-2023-01.pdf"},
                {"month": "February", "url": f"https://mockbank.com/statements/{account_id}-2023-02.pdf"},
                {"month": "March", "url": f"https://mockbank.com/statements/{account_id}-2023-03.pdf"},
                {"month": "April", "url": f"https://mockbank.com/statements/{account_id}-2023-04.pdf"},
                {"month": "May", "url": f"https://mockbank.com/statements/{account_id}-2023-05.pdf"},
                {"month": "June", "url": f"https://mockbank.com/statements/{account_id}-2023-06.pdf"},
                {"month": "July", "url": f"https://mockbank.com/statements/{account_id}-2023-07.pdf"},
                {"month": "August", "url": f"https://mockbank.com/statements/{account_id}-2023-08.pdf"},
                {"month": "September", "url": f"https://mockbank.com/statements/{account_id}-2023-09.pdf"},
                {"month": "October", "url": f"https://mockbank.com/statements/{account_id}-2023-10.pdf"},
            ],
            2022: [
                {"month": "December", "url": f"https://mockbank.com/statements/{account_id}-2022-12.pdf"}
            ]
        },
        "SAV-002": {
            2023: [
                {"month": "October", "url": f"https://mockbank.com/statements/{account_id}-2023-10.pdf"}
            ]
        }
    }

    account_statements = mock_statements_data.get(account_id, {})
    statements_for_year = account_statements.get(target_year, [])

    if statements_for_year:
        # Add year to each statement dict for clarity
        for stmt in statements_for_year:
            stmt["year"] = target_year
        return {"statements": statements_for_year}
    else:
        return {"error": True, "message": f"No statements found for account '{account_id}' in year {target_year}."}