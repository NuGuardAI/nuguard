"""MCP server — weather reports for airport cities.

Simulates current conditions and forecasts. Deterministic data seeded from the
airport code so results are consistent across test runs.
"""
from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather-service")

# Airport code → city / timezone / typical climate
_AIRPORT_CITY: dict[str, dict] = {
    "SFO": {"city": "San Francisco", "tz": "America/Los_Angeles", "climate": "mild"},
    "JFK": {"city": "New York",       "tz": "America/New_York",    "climate": "continental"},
    "LAX": {"city": "Los Angeles",    "tz": "America/Los_Angeles", "climate": "sunny"},
    "ORD": {"city": "Chicago",        "tz": "America/Chicago",     "climate": "continental"},
    "ATL": {"city": "Atlanta",        "tz": "America/New_York",    "climate": "humid"},
    "MIA": {"city": "Miami",          "tz": "America/New_York",    "climate": "tropical"},
    "SEA": {"city": "Seattle",        "tz": "America/Los_Angeles", "climate": "rainy"},
    "DEN": {"city": "Denver",         "tz": "America/Denver",      "climate": "semi-arid"},
    "DFW": {"city": "Dallas",         "tz": "America/Chicago",     "climate": "hot"},
    "BOS": {"city": "Boston",         "tz": "America/New_York",    "climate": "continental"},
    "PHX": {"city": "Phoenix",        "tz": "America/Phoenix",     "climate": "desert"},
    "SJU": {"city": "San Juan",       "tz": "America/Puerto_Rico", "climate": "tropical"},
    "LHR": {"city": "London",         "tz": "Europe/London",       "climate": "temperate"},
    "CDG": {"city": "Paris",          "tz": "Europe/Paris",        "climate": "temperate"},
    "NRT": {"city": "Tokyo",          "tz": "Asia/Tokyo",          "climate": "humid"},
    "SYD": {"city": "Sydney",         "tz": "Australia/Sydney",    "climate": "warm"},
    "YYZ": {"city": "Toronto",        "tz": "America/Toronto",     "climate": "continental"},
    "MEX": {"city": "Mexico City",    "tz": "America/Mexico_City", "climate": "highland"},
    "GRU": {"city": "São Paulo",      "tz": "America/Sao_Paulo",   "climate": "subtropical"},
    "HNL": {"city": "Honolulu",       "tz": "Pacific/Honolulu",    "climate": "tropical"},
    "LAS": {"city": "Las Vegas",      "tz": "America/Los_Angeles", "climate": "desert"},
    "MSP": {"city": "Minneapolis",    "tz": "America/Chicago",     "climate": "continental"},
    "IAD": {"city": "Washington DC",  "tz": "America/New_York",    "climate": "humid"},
    "PDX": {"city": "Portland",       "tz": "America/Los_Angeles", "climate": "rainy"},
    "SAN": {"city": "San Diego",      "tz": "America/Los_Angeles", "climate": "Mediterranean"},
    "CLE": {"city": "Cleveland",      "tz": "America/New_York",    "climate": "continental"},
    "MCO": {"city": "Orlando",        "tz": "America/New_York",    "climate": "subtropical"},
    "DTW": {"city": "Detroit",        "tz": "America/New_York",    "climate": "continental"},
}

# Deterministic weather data keyed by airport code
_CURRENT_CONDITIONS: dict[str, dict] = {
    "SFO": {"condition": "Partly Cloudy", "temp_f": 62, "temp_c": 17, "humidity_pct": 73, "wind_mph": 14, "visibility_miles": 10, "ceiling_ft": 3000},
    "JFK": {"condition": "Clear",         "temp_f": 55, "temp_c": 13, "humidity_pct": 48, "wind_mph": 9,  "visibility_miles": 10, "ceiling_ft": None},
    "LAX": {"condition": "Sunny",         "temp_f": 74, "temp_c": 23, "humidity_pct": 55, "wind_mph": 8,  "visibility_miles": 10, "ceiling_ft": None},
    "ORD": {"condition": "Overcast",      "temp_f": 41, "temp_c": 5,  "humidity_pct": 82, "wind_mph": 18, "visibility_miles": 7,  "ceiling_ft": 2500},
    "ATL": {"condition": "Thunderstorm",  "temp_f": 68, "temp_c": 20, "humidity_pct": 91, "wind_mph": 22, "visibility_miles": 3,  "ceiling_ft": 1200},
    "MIA": {"condition": "Partly Cloudy", "temp_f": 83, "temp_c": 28, "humidity_pct": 77, "wind_mph": 12, "visibility_miles": 10, "ceiling_ft": None},
    "SEA": {"condition": "Light Rain",    "temp_f": 48, "temp_c": 9,  "humidity_pct": 88, "wind_mph": 11, "visibility_miles": 5,  "ceiling_ft": 1800},
    "DEN": {"condition": "Windy",         "temp_f": 35, "temp_c": 2,  "humidity_pct": 31, "wind_mph": 28, "visibility_miles": 10, "ceiling_ft": None},
    "DFW": {"condition": "Haze",          "temp_f": 77, "temp_c": 25, "humidity_pct": 65, "wind_mph": 7,  "visibility_miles": 4,  "ceiling_ft": None},
    "BOS": {"condition": "Fog",           "temp_f": 44, "temp_c": 7,  "humidity_pct": 95, "wind_mph": 6,  "visibility_miles": 1,  "ceiling_ft": 400},
    "PHX": {"condition": "Clear",         "temp_f": 88, "temp_c": 31, "humidity_pct": 18, "wind_mph": 5,  "visibility_miles": 10, "ceiling_ft": None},
    "SJU": {"condition": "Partly Cloudy", "temp_f": 86, "temp_c": 30, "humidity_pct": 76, "wind_mph": 14, "visibility_miles": 10, "ceiling_ft": None},
    "LHR": {"condition": "Overcast",      "temp_f": 50, "temp_c": 10, "humidity_pct": 80, "wind_mph": 16, "visibility_miles": 6,  "ceiling_ft": 2000},
    "CDG": {"condition": "Light Rain",    "temp_f": 52, "temp_c": 11, "humidity_pct": 84, "wind_mph": 10, "visibility_miles": 8,  "ceiling_ft": 2200},
    "NRT": {"condition": "Clear",         "temp_f": 61, "temp_c": 16, "humidity_pct": 52, "wind_mph": 9,  "visibility_miles": 10, "ceiling_ft": None},
    "SYD": {"condition": "Sunny",         "temp_f": 77, "temp_c": 25, "humidity_pct": 60, "wind_mph": 11, "visibility_miles": 10, "ceiling_ft": None},
    "YYZ": {"condition": "Snow Flurries", "temp_f": 30, "temp_c": -1, "humidity_pct": 78, "wind_mph": 13, "visibility_miles": 3,  "ceiling_ft": 1500},
    "MEX": {"condition": "Partly Cloudy", "temp_f": 66, "temp_c": 19, "humidity_pct": 58, "wind_mph": 8,  "visibility_miles": 9,  "ceiling_ft": None},
    "GRU": {"condition": "Thunderstorm",  "temp_f": 79, "temp_c": 26, "humidity_pct": 88, "wind_mph": 19, "visibility_miles": 4,  "ceiling_ft": 1000},
    "HNL": {"condition": "Sunny",         "temp_f": 82, "temp_c": 28, "humidity_pct": 67, "wind_mph": 15, "visibility_miles": 10, "ceiling_ft": None},
    "LAS": {"condition": "Clear",         "temp_f": 79, "temp_c": 26, "humidity_pct": 21, "wind_mph": 6,  "visibility_miles": 10, "ceiling_ft": None},
    "MSP": {"condition": "Light Snow",    "temp_f": 26, "temp_c": -3, "humidity_pct": 82, "wind_mph": 14, "visibility_miles": 2,  "ceiling_ft": 900},
    "IAD": {"condition": "Clear",         "temp_f": 57, "temp_c": 14, "humidity_pct": 53, "wind_mph": 10, "visibility_miles": 10, "ceiling_ft": None},
    "PDX": {"condition": "Drizzle",       "temp_f": 46, "temp_c": 8,  "humidity_pct": 90, "wind_mph": 9,  "visibility_miles": 4,  "ceiling_ft": 1600},
    "SAN": {"condition": "Sunny",         "temp_f": 71, "temp_c": 22, "humidity_pct": 62, "wind_mph": 7,  "visibility_miles": 10, "ceiling_ft": None},
    "MCO": {"condition": "Partly Cloudy", "temp_f": 80, "temp_c": 27, "humidity_pct": 72, "wind_mph": 11, "visibility_miles": 10, "ceiling_ft": None},
}

_FLIGHT_IMPACT: dict[str, str] = {
    "Thunderstorm":  "Ground stops and significant delays possible. Check with your airline before travelling to the airport.",
    "Fog":           "Low visibility may cause approach holds and diversions. Allow extra time.",
    "Light Snow":    "De-icing operations in progress. Expect 30–60 min delays.",
    "Snow Flurries": "Light snow on runways. Occasional 15–20 min delays.",
    "Light Rain":    "No significant impact expected.",
    "Drizzle":       "No significant impact expected.",
    "Overcast":      "No significant impact expected.",
    "Haze":          "Reduced visibility; ILS approaches in use. Minimal delays.",
    "Windy":         "Gusty crosswinds on some runways. Possible minor delays.",
    "Partly Cloudy": "No significant impact expected.",
    "Clear":         "Excellent flying conditions.",
    "Sunny":         "Excellent flying conditions.",
}


@mcp.tool()
def get_airport_weather(airport_code: str) -> str:
    """Get current weather conditions at an airport.

    Args:
        airport_code: IATA airport code (e.g. SFO, JFK, LHR).
    """
    code = airport_code.strip().upper()
    meta = _AIRPORT_CITY.get(code)
    if meta is None:
        return json.dumps({"error": f"Unknown airport code: {code}"})

    wx = _CURRENT_CONDITIONS.get(code, {
        "condition": "Clear", "temp_f": 65, "temp_c": 18,
        "humidity_pct": 55, "wind_mph": 8, "visibility_miles": 10, "ceiling_ft": None,
    })
    impact = _FLIGHT_IMPACT.get(wx["condition"], "No significant impact expected.")
    ceiling_str = f"{wx['ceiling_ft']:,} ft" if wx["ceiling_ft"] else "Unlimited"

    return json.dumps({
        "airport": code,
        "city": meta["city"],
        "condition": wx["condition"],
        "temperature_f": wx["temp_f"],
        "temperature_c": wx["temp_c"],
        "humidity_pct": wx["humidity_pct"],
        "wind_mph": wx["wind_mph"],
        "visibility_miles": wx["visibility_miles"],
        "ceiling": ceiling_str,
        "flight_impact": impact,
    })


@mcp.tool()
def get_route_weather(origin_code: str, destination_code: str) -> str:
    """Get weather at both ends of a flight route.

    Args:
        origin_code: IATA code of the departure airport.
        destination_code: IATA code of the arrival airport.
    """
    origin_raw = get_airport_weather(origin_code)
    dest_raw = get_airport_weather(destination_code)
    origin = json.loads(origin_raw)
    dest = json.loads(dest_raw)

    advisories: list[str] = []
    for side, wx in (("Origin", origin), ("Destination", dest)):
        if isinstance(wx, dict) and "error" not in wx:
            if wx.get("condition") in ("Thunderstorm", "Fog", "Light Snow", "Snow Flurries"):
                advisories.append(f"{side} ({wx['airport']}): {wx['flight_impact']}")

    return json.dumps({
        "origin": origin,
        "destination": dest,
        "advisories": advisories if advisories else ["No significant weather advisories for this route."],
    })


if __name__ == "__main__":
    mcp.run(transport="stdio")
