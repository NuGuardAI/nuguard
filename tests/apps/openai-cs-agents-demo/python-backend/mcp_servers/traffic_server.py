"""MCP server — road traffic reports for routes to/from airports.

Simulates real-time traffic conditions on major airport access roads.
Data is deterministic so test runs are reproducible.
"""
from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("traffic-service")

# Airport → access roads with current conditions
_AIRPORT_ROADS: dict[str, list[dict]] = {
    "SFO": [
        {"road": "US-101 Northbound (SFO to SF)", "condition": "Heavy traffic", "delay_min": 35, "incident": None},
        {"road": "US-101 Southbound (SFO to Peninsula)", "condition": "Moderate traffic", "delay_min": 12, "incident": None},
        {"road": "I-380 / Millbrae Ave", "condition": "Clear", "delay_min": 0, "incident": None},
    ],
    "JFK": [
        {"road": "Van Wyck Expressway (I-678)", "condition": "Stop-and-go", "delay_min": 55, "incident": "Stalled vehicle near Belt Pkwy"},
        {"road": "Belt Parkway Eastbound", "condition": "Heavy traffic", "delay_min": 30, "incident": None},
        {"road": "AirTrain to Jamaica Station", "condition": "Normal service", "delay_min": 0, "incident": None},
    ],
    "LAX": [
        {"road": "I-105 Eastbound (Century Freeway)", "condition": "Moderate traffic", "delay_min": 18, "incident": None},
        {"road": "Sepulveda Blvd (Airport access)", "condition": "Heavy traffic", "delay_min": 25, "incident": "Road work — one lane closed"},
        {"road": "I-405 Northbound", "condition": "Severe congestion", "delay_min": 60, "incident": "Multi-vehicle accident"},
    ],
    "ORD": [
        {"road": "I-190 / O'Hare Expressway", "condition": "Heavy traffic", "delay_min": 28, "incident": None},
        {"road": "I-90 / I-94 (Chicagoland Expressway)", "condition": "Moderate traffic", "delay_min": 15, "incident": None},
        {"road": "IL-72 (Higgins Road)", "condition": "Clear", "delay_min": 0, "incident": None},
    ],
    "ATL": [
        {"road": "I-85 Northbound", "condition": "Heavy traffic", "delay_min": 40, "incident": "Accident — two right lanes closed"},
        {"road": "I-75 / I-85 Downtown Connector", "condition": "Stop-and-go", "delay_min": 45, "incident": None},
        {"road": "GA-139 / Camp Creek Pkwy", "condition": "Moderate traffic", "delay_min": 10, "incident": None},
    ],
    "MIA": [
        {"road": "SR-836 / Dolphin Expressway", "condition": "Moderate traffic", "delay_min": 14, "incident": None},
        {"road": "Le Jeune Rd / NW 42nd Ave", "condition": "Clear", "delay_min": 0, "incident": None},
        {"road": "I-95 Southbound", "condition": "Moderate traffic", "delay_min": 20, "incident": None},
    ],
    "SEA": [
        {"road": "I-5 Northbound (SeaTac to Seattle)", "condition": "Heavy traffic", "delay_min": 32, "incident": None},
        {"road": "WA-518 / International Blvd", "condition": "Moderate traffic", "delay_min": 12, "incident": None},
        {"road": "WA-99 (Pacific Hwy S)", "condition": "Clear", "delay_min": 0, "incident": None},
    ],
    "DEN": [
        {"road": "I-70 Eastbound (DIA to Denver)", "condition": "Clear", "delay_min": 0, "incident": None},
        {"road": "Peña Blvd", "condition": "Clear", "delay_min": 0, "incident": None},
        {"road": "I-225 Southbound", "condition": "Moderate traffic", "delay_min": 8, "incident": None},
    ],
    "DFW": [
        {"road": "TX-183 / Airport Freeway", "condition": "Heavy traffic", "delay_min": 30, "incident": None},
        {"road": "I-635 / LBJ Freeway Westbound", "condition": "Stop-and-go", "delay_min": 50, "incident": "Construction — reduced to two lanes"},
        {"road": "TX-97 / Esters Blvd", "condition": "Moderate traffic", "delay_min": 10, "incident": None},
    ],
    "BOS": [
        {"road": "I-93 Northbound (Sumner Tunnel)", "condition": "Heavy traffic", "delay_min": 25, "incident": None},
        {"road": "Ted Williams Tunnel (I-90 E)", "condition": "Moderate traffic", "delay_min": 15, "incident": None},
        {"road": "MA-1A / McClellan Hwy", "condition": "Clear", "delay_min": 0, "incident": None},
    ],
    "PHX": [
        {"road": "I-10 Eastbound (Sky Harbor Blvd)", "condition": "Clear", "delay_min": 0, "incident": None},
        {"road": "AZ-143 / Hohokam Expressway", "condition": "Clear", "delay_min": 0, "incident": None},
        {"road": "24th St (PHX Terminal 4 access)", "condition": "Moderate traffic", "delay_min": 8, "incident": None},
    ],
    "SJU": [
        {"road": "PR-26 / Luis A. Ferré Expressway", "condition": "Moderate traffic", "delay_min": 15, "incident": None},
        {"road": "Isla Verde Ave", "condition": "Heavy traffic", "delay_min": 20, "incident": None},
    ],
    "LHR": [
        {"road": "M4 Eastbound (Heathrow to London)", "condition": "Heavy traffic", "delay_min": 45, "incident": None},
        {"road": "M25 Clockwise (J14–J15)", "condition": "Stop-and-go", "delay_min": 55, "incident": "Lane closure — resurfacing"},
        {"road": "Heathrow Express (Paddington)", "condition": "Normal service", "delay_min": 0, "incident": None},
    ],
    "MCO": [
        {"road": "FL-528 / Beachline Expressway", "condition": "Clear", "delay_min": 0, "incident": None},
        {"road": "I-4 Westbound", "condition": "Heavy traffic", "delay_min": 30, "incident": "Accident near downtown"},
        {"road": "Airport Blvd", "condition": "Moderate traffic", "delay_min": 10, "incident": None},
    ],
}

_DEFAULT_ROADS: list[dict] = [
    {"road": "Primary access road", "condition": "Clear", "delay_min": 0, "incident": None},
]

_RECOMMENDATION: dict[str, str] = {
    "Clear":             "Traffic is flowing freely. Normal travel time to the airport.",
    "Moderate traffic":  "Allow an extra 15–20 minutes.",
    "Heavy traffic":     "Allow an extra 30–45 minutes. Consider public transit if available.",
    "Stop-and-go":       "Severe congestion. Add 45–60 minutes or use public transit.",
}


@mcp.tool()
def get_airport_traffic(airport_code: str) -> str:
    """Get current road traffic conditions around an airport.

    Args:
        airport_code: IATA airport code (e.g. SFO, JFK, LHR).
    """
    code = airport_code.strip().upper()
    roads = _AIRPORT_ROADS.get(code, _DEFAULT_ROADS)

    worst_delay = max(r["delay_min"] for r in roads)
    if worst_delay == 0:
        overall = "Clear"
    elif worst_delay <= 20:
        overall = "Moderate traffic"
    elif worst_delay <= 40:
        overall = "Heavy traffic"
    else:
        overall = "Stop-and-go"

    incidents = [r for r in roads if r.get("incident")]
    recommendation = _RECOMMENDATION.get(overall, "Check local traffic apps for live updates.")

    return json.dumps({
        "airport": code,
        "overall_condition": overall,
        "worst_delay_min": worst_delay,
        "roads": roads,
        "active_incidents": [r["incident"] for r in incidents],
        "recommendation": recommendation,
    })


@mcp.tool()
def get_airport_parking(airport_code: str) -> str:
    """Get parking availability and recommended arrival times at an airport.

    Args:
        airport_code: IATA airport code (e.g. SFO, JFK, LHR).
    """
    _PARKING: dict[str, dict] = {
        "SFO": {"short_term": "Available", "long_term": "Limited", "daily_rate_usd": 35, "recommended_arrival_min_before": 120},
        "JFK": {"short_term": "Limited",   "long_term": "Available", "daily_rate_usd": 42, "recommended_arrival_min_before": 180},
        "LAX": {"short_term": "Full",       "long_term": "Limited",  "daily_rate_usd": 40, "recommended_arrival_min_before": 180},
        "ORD": {"short_term": "Available",  "long_term": "Available","daily_rate_usd": 30, "recommended_arrival_min_before": 120},
        "ATL": {"short_term": "Limited",    "long_term": "Available","daily_rate_usd": 28, "recommended_arrival_min_before": 150},
        "MIA": {"short_term": "Available",  "long_term": "Available","daily_rate_usd": 35, "recommended_arrival_min_before": 120},
        "SEA": {"short_term": "Available",  "long_term": "Limited",  "daily_rate_usd": 32, "recommended_arrival_min_before": 120},
        "DEN": {"short_term": "Available",  "long_term": "Available","daily_rate_usd": 26, "recommended_arrival_min_before": 90},
        "LHR": {"short_term": "Limited",    "long_term": "Available","daily_rate_usd": 65, "recommended_arrival_min_before": 180},
        "PHX": {"short_term": "Available",  "long_term": "Available","daily_rate_usd": 22, "recommended_arrival_min_before": 90},
    }
    code = airport_code.strip().upper()
    data = _PARKING.get(code, {
        "short_term": "Available", "long_term": "Available",
        "daily_rate_usd": 30, "recommended_arrival_min_before": 120,
    })
    return json.dumps({"airport": code, **data})


if __name__ == "__main__":
    mcp.run(transport="stdio")
