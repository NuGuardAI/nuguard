"""MCP server — live flight status and gate information.

Simulates real-time operational flight data including gate changes,
delays, baggage claim, and FIDS-style status updates.
Data is deterministic so test runs are reproducible.
"""
from __future__ import annotations

import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("flight-live-service")

# Simulated live operational data (supplements the SBOM/DB static records)
_LIVE_FLIGHT_STATUS: dict[str, dict] = {
    "FLT-123": {
        "flight_number": "FLT-123",
        "origin": "SFO", "destination": "JFK",
        "scheduled_departure": "2026-04-20T08:30:00",
        "estimated_departure": "2026-04-20T08:30:00",
        "scheduled_arrival": "2026-04-20T17:05:00",
        "estimated_arrival": "2026-04-20T17:05:00",
        "status": "On Time",
        "gate": "A10",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Airbus A220",
        "aircraft_registration": "N523NG",
        "meals_served": "Full meal service",
        "wifi": True,
    },
    "FLT-476": {
        "flight_number": "FLT-476",
        "origin": "SEA", "destination": "ORD",
        "scheduled_departure": "2026-04-21T12:15:00",
        "estimated_departure": "2026-04-21T12:50:00",
        "scheduled_arrival": "2026-04-21T18:20:00",
        "estimated_arrival": "2026-04-21T18:55:00",
        "status": "Delayed 35 minutes",
        "gate": "C7",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": "Late-arriving aircraft from previous segment",
        "aircraft_type": "Boeing 737",
        "aircraft_registration": "N737MW",
        "meals_served": "Snacks and beverages",
        "wifi": True,
    },
    "FLT-789": {
        "flight_number": "FLT-789",
        "origin": "LAX", "destination": "DEN",
        "scheduled_departure": "2026-04-22T09:45:00",
        "estimated_departure": "2026-04-22T09:45:00",
        "scheduled_arrival": "2026-04-22T13:05:00",
        "estimated_arrival": "2026-04-22T13:05:00",
        "status": "Boarding",
        "gate": "B4",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Airbus A320",
        "aircraft_registration": "N320LA",
        "meals_served": "Buy on board",
        "wifi": True,
    },
    "FLT-245": {
        "flight_number": "FLT-245",
        "origin": "ATL", "destination": "MIA",
        "scheduled_departure": "2026-04-23T16:10:00",
        "estimated_departure": "2026-04-23T16:10:00",
        "scheduled_arrival": "2026-04-23T18:05:00",
        "estimated_arrival": "2026-04-23T18:05:00",
        "status": "On Time",
        "gate": "D12",
        "gate_change": None,
        "baggage_claim": "Carousel 7",
        "delay_reason": None,
        "aircraft_type": "Embraer 175",
        "aircraft_registration": "N175AT",
        "meals_served": "Snacks and beverages",
        "wifi": False,
    },
    "FLT-302": {
        "flight_number": "FLT-302",
        "origin": "BOS", "destination": "SFO",
        "scheduled_departure": "2026-04-24T07:20:00",
        "estimated_departure": "2026-04-24T08:05:00",
        "scheduled_arrival": "2026-04-24T10:55:00",
        "estimated_arrival": "2026-04-24T11:40:00",
        "status": "Delayed 45 minutes",
        "gate": "E3",
        "gate_change": "E3 → E7",
        "baggage_claim": None,
        "delay_reason": "Low visibility fog at origin — waiting for IFR clearance",
        "aircraft_type": "Boeing 757",
        "aircraft_registration": "N757BX",
        "meals_served": "Full meal service",
        "wifi": True,
    },
    "FLT-618": {
        "flight_number": "FLT-618",
        "origin": "JFK", "destination": "LHR",
        "scheduled_departure": "2026-04-25T19:40:00",
        "estimated_departure": "2026-04-25T19:40:00",
        "scheduled_arrival": "2026-04-26T07:35:00",
        "estimated_arrival": "2026-04-26T07:35:00",
        "status": "Scheduled",
        "gate": "A2",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Boeing 787-9",
        "aircraft_registration": "N789NG",
        "meals_served": "Full meal service (business) / dinner and breakfast (economy)",
        "wifi": True,
    },
    "FLT-904": {
        "flight_number": "FLT-904",
        "origin": "DFW", "destination": "PHX",
        "scheduled_departure": "2026-04-26T14:05:00",
        "estimated_departure": None,
        "scheduled_arrival": "2026-04-26T15:45:00",
        "estimated_arrival": None,
        "status": "Cancelled",
        "gate": "C12",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": "Mechanical issue — aircraft pulled from service",
        "aircraft_type": "Airbus A319",
        "aircraft_registration": "N319DF",
        "meals_served": "Snacks and beverages",
        "wifi": False,
    },
    "FLT-551": {
        "flight_number": "FLT-551",
        "origin": "MIA", "destination": "SJU",
        "scheduled_departure": "2026-04-27T11:30:00",
        "estimated_departure": "2026-04-27T11:30:00",
        "scheduled_arrival": "2026-04-27T14:05:00",
        "estimated_arrival": "2026-04-27T14:05:00",
        "status": "On Time",
        "gate": "H6",
        "gate_change": None,
        "baggage_claim": "Carousel 2",
        "delay_reason": None,
        "aircraft_type": "Airbus A321",
        "aircraft_registration": "N321MX",
        "meals_served": "Snacks and beverages",
        "wifi": True,
    },
    "FLT-842": {
        "flight_number": "FLT-842",
        "origin": "ORD", "destination": "SEA",
        "scheduled_departure": "2026-04-28T06:15:00",
        "estimated_departure": "2026-04-28T06:15:00",
        "scheduled_arrival": "2026-04-28T08:55:00",
        "estimated_arrival": "2026-04-28T08:55:00",
        "status": "Scheduled",
        "gate": "K18",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Boeing 737 MAX 8",
        "aircraft_registration": "N8MAX1",
        "meals_served": "Buy on board",
        "wifi": True,
    },
    "FLT-330": {
        "flight_number": "FLT-330",
        "origin": "DEN", "destination": "SFO",
        "scheduled_departure": "2026-04-29T17:25:00",
        "estimated_departure": "2026-04-29T17:25:00",
        "scheduled_arrival": "2026-04-29T19:10:00",
        "estimated_arrival": "2026-04-29T19:10:00",
        "status": "On Time",
        "gate": "B9",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Airbus A320",
        "aircraft_registration": "N320DV",
        "meals_served": "Snacks and beverages",
        "wifi": True,
    },
    # Extended flights added in the new data set
    "FLT-115": {
        "flight_number": "FLT-115",
        "origin": "LAX", "destination": "NRT",
        "scheduled_departure": "2026-04-30T13:55:00",
        "estimated_departure": "2026-04-30T14:30:00",
        "scheduled_arrival": "2026-05-01T18:20:00",
        "estimated_arrival": "2026-05-01T18:55:00",
        "status": "Delayed 35 minutes",
        "gate": "T4-B6",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": "Crew scheduling adjustment",
        "aircraft_type": "Boeing 787-10",
        "aircraft_registration": "N787LA",
        "meals_served": "Full meal service (two meals, one snack)",
        "wifi": True,
    },
    "FLT-720": {
        "flight_number": "FLT-720",
        "origin": "JFK", "destination": "CDG",
        "scheduled_departure": "2026-05-01T21:30:00",
        "estimated_departure": "2026-05-01T21:30:00",
        "scheduled_arrival": "2026-05-02T11:05:00",
        "estimated_arrival": "2026-05-02T11:05:00",
        "status": "Scheduled",
        "gate": "B22",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Airbus A330-300",
        "aircraft_registration": "N330JF",
        "meals_served": "Full meal service (dinner and breakfast)",
        "wifi": True,
    },
    "FLT-403": {
        "flight_number": "FLT-403",
        "origin": "SFO", "destination": "HNL",
        "scheduled_departure": "2026-05-02T10:20:00",
        "estimated_departure": "2026-05-02T10:20:00",
        "scheduled_arrival": "2026-05-02T14:40:00",
        "estimated_arrival": "2026-05-02T14:40:00",
        "status": "On Time",
        "gate": "F12",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Airbus A321XLR",
        "aircraft_registration": "N321SH",
        "meals_served": "Full meal service",
        "wifi": True,
    },
    "FLT-655": {
        "flight_number": "FLT-655",
        "origin": "ORD", "destination": "LHR",
        "scheduled_departure": "2026-05-03T17:15:00",
        "estimated_departure": "2026-05-03T18:40:00",
        "scheduled_arrival": "2026-05-04T07:30:00",
        "estimated_arrival": "2026-05-04T08:55:00",
        "status": "Delayed 85 minutes",
        "gate": "K2",
        "gate_change": "K2 → K9",
        "baggage_claim": None,
        "delay_reason": "Inbound aircraft delayed due to European ATC restrictions",
        "aircraft_type": "Boeing 777-200ER",
        "aircraft_registration": "N777CG",
        "meals_served": "Full meal service (business) / dinner and breakfast (economy)",
        "wifi": True,
    },
    "FLT-218": {
        "flight_number": "FLT-218",
        "origin": "MCO", "destination": "BOS",
        "scheduled_departure": "2026-05-04T08:00:00",
        "estimated_departure": "2026-05-04T08:00:00",
        "scheduled_arrival": "2026-05-04T11:45:00",
        "estimated_arrival": "2026-05-04T11:45:00",
        "status": "On Time",
        "gate": "G3",
        "gate_change": None,
        "baggage_claim": None,
        "delay_reason": None,
        "aircraft_type": "Boeing 737-800",
        "aircraft_registration": "N738MC",
        "meals_served": "Buy on board",
        "wifi": True,
    },
}


@mcp.tool()
def get_live_flight_status(flight_number: str) -> str:
    """Get real-time operational status for a flight including gate, delays, and aircraft info.

    Args:
        flight_number: Flight number (e.g. FLT-123).
    """
    fnum = flight_number.strip().upper()
    data = _LIVE_FLIGHT_STATUS.get(fnum)
    if data is None:
        return json.dumps({"error": f"No live data found for flight {fnum}. The flight may not be in today's schedule."})
    return json.dumps(data)


@mcp.tool()
def get_departure_board(airport_code: str) -> str:
    """Get the live departure board for an airport showing all upcoming departures.

    Args:
        airport_code: IATA code of the departure airport (e.g. SFO).
    """
    code = airport_code.strip().upper()
    departures = [
        {
            "flight_number": v["flight_number"],
            "destination": v["destination"],
            "scheduled_departure": v["scheduled_departure"],
            "estimated_departure": v["estimated_departure"],
            "status": v["status"],
            "gate": v["gate"],
            "gate_change": v.get("gate_change"),
        }
        for v in _LIVE_FLIGHT_STATUS.values()
        if v["origin"] == code
    ]
    departures.sort(key=lambda x: x["scheduled_departure"])
    if not departures:
        return json.dumps({"airport": code, "departures": [], "note": "No departures found for this airport in today's schedule."})
    return json.dumps({"airport": code, "departures": departures})


@mcp.tool()
def get_arrival_board(airport_code: str) -> str:
    """Get the live arrivals board for an airport.

    Args:
        airport_code: IATA code of the arrival airport (e.g. JFK).
    """
    code = airport_code.strip().upper()
    arrivals = [
        {
            "flight_number": v["flight_number"],
            "origin": v["origin"],
            "scheduled_arrival": v["scheduled_arrival"],
            "estimated_arrival": v["estimated_arrival"],
            "status": v["status"],
            "baggage_claim": v.get("baggage_claim"),
        }
        for v in _LIVE_FLIGHT_STATUS.values()
        if v["destination"] == code
    ]
    arrivals.sort(key=lambda x: x["scheduled_arrival"])
    if not arrivals:
        return json.dumps({"airport": code, "arrivals": [], "note": "No arrivals found for this airport in today's schedule."})
    return json.dumps({"airport": code, "arrivals": arrivals})


@mcp.tool()
def get_gate_change_alerts(airport_code: str) -> str:
    """Get all active gate change alerts at an airport.

    Args:
        airport_code: IATA code (e.g. ORD).
    """
    code = airport_code.strip().upper()
    changes = [
        {
            "flight_number": v["flight_number"],
            "destination": v["destination"],
            "old_gate": v["gate_change"].split(" → ")[0] if v.get("gate_change") else v["gate"],
            "new_gate": v["gate_change"].split(" → ")[1] if v.get("gate_change") else v["gate"],
            "departure": v["scheduled_departure"],
        }
        for v in _LIVE_FLIGHT_STATUS.values()
        if v["origin"] == code and v.get("gate_change")
    ]
    return json.dumps({
        "airport": code,
        "gate_changes": changes,
        "count": len(changes),
    })


if __name__ == "__main__":
    mcp.run(transport="stdio")
