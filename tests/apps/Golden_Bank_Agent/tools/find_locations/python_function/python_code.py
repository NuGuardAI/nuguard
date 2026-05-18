from typing import Any, Optional

def find_locations(
    location_type: str,
    zip_code: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None
) -> dict[str, Any]:
    """
    Finds nearby ATM or branch locations based on a provided zip code or geolocation.

    Args:
        location_type (str): The type of location to search for ("ATM" or "Branch").
        zip_code (Optional[str]): The zip code to search within.
        latitude (Optional[float]): The latitude of the user's current location.
        longitude (Optional[float]): The longitude of the user's current location.

    Returns:
        dict[str, Any]: A dictionary containing a list of found locations.
              Example: {"locations": [{"name": "Golden Bank ATM - Downtown", "address": "123 Main St, City, State 12345"}]}
              Example: {"error": True, "message": "Please provide a zip code or your current location."}
    """
    # MOCK: This mock provides predefined locations based on zip code.
    # In a real scenario, this would integrate with a mapping service API.
    mock_locations_data = {
        "90210": {
            "ATM": [
                {"name": "Golden Bank ATM - Beverly Hills", "address": "123 Main St, Beverly Hills, CA 90210"},
                {"name": "Golden Bank ATM - Rodeo Dr", "address": "456 Rodeo Dr, Beverly Hills, CA 90210"}
            ],
            "Branch": [
                {"name": "Golden Bank Branch - Beverly Hills", "address": "789 Oak Ave, Beverly Hills, CA 90210"}
            ]
        },
        "10001": {
            "ATM": [
                {"name": "Golden Bank ATM - Midtown", "address": "100 Broadway, New York, NY 10001"}
            ],
            "Branch": [
                {"name": "Golden Bank Branch - Empire State", "address": "200 Park Ave, New York, NY 10001"}
            ]
        }
    }

    if location_type not in ["ATM", "Branch"]:
        return {"error": True, "message": "Invalid location type. Please specify 'ATM' or 'Branch'."}

    if zip_code:
        locations = mock_locations_data.get(zip_code, {}).get(location_type, [])
        if locations:
            return {"locations": locations}
        else:
            return {"locations": [], "message": f"No {location_type} locations found for zip code {zip_code}."}
    elif latitude is not None and longitude is not None:
        # For a mock, we can just return a generic location or a specific one for a mock lat/long
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            # Simulate finding a location near a mock coordinate
            return {"locations": [{"name": f"Golden Bank {location_type} - Near You", "address": "100 Mock St, Mockville, CA 90210"}]}
        else:
            return {"error": True, "message": "Invalid latitude or longitude provided."}
    else:
        return {"error": True, "message": "Please provide a zip code or your current location (latitude and longitude) to find locations."}