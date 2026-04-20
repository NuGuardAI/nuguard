from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any


DB_PATH = Path(os.getenv("AIRLINE_DEMO_DB", Path(__file__).with_name("airline_demo.sqlite3")))
PASSWORD_SALT = "openai-cs-agents-demo"


def hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        PASSWORD_SALT.encode("utf-8"),
        100_000,
    ).hex()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                account_number TEXT NOT NULL UNIQUE,
                username TEXT UNIQUE,
                password_hash TEXT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                loyalty_tier TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS flights (
                id INTEGER PRIMARY KEY,
                flight_number TEXT NOT NULL UNIQUE,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                departure_time TEXT NOT NULL,
                arrival_time TEXT NOT NULL,
                status TEXT NOT NULL,
                gate TEXT NOT NULL,
                aircraft TEXT NOT NULL,
                seat_capacity INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY,
                confirmation_number TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id),
                flight_id INTEGER NOT NULL REFERENCES flights(id),
                seat_number TEXT NOT NULL,
                status TEXT NOT NULL,
                checked_bags INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                UNIQUE(category, title)
            );
            """
        )
        existing_user_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "username" not in existing_user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "password_hash" not in existing_user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )

        conn.executemany(
            """
            INSERT OR IGNORE INTO users
                (id, account_number, username, password_hash, full_name, email, loyalty_tier)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "10000001", "avery", hash_password("avery-pass"), "Avery Stone", "avery.stone@example.com", "Gold"),
                (2, "10000002", "mina", hash_password("mina-pass"), "Mina Chen", "mina.chen@example.com", "Silver"),
                (3, "10000003", "jordan", hash_password("jordan-pass"), "Jordan Patel", "jordan.patel@example.com", "Platinum"),
                (4, "10000004", "sam", hash_password("sam-pass"), "Sam Rivera", "sam.rivera@example.com", "Basic"),
                (5, "10000005", "nora", hash_password("nora-pass"), "Nora Brooks", "nora.brooks@example.com", "Gold"),
                (6, "10000006", "eli", hash_password("eli-pass"), "Eli Morgan", "eli.morgan@example.com", "Silver"),
                (7, "10000007", "priya", hash_password("priya-pass"), "Priya Shah", "priya.shah@example.com", "Platinum"),
                (8, "10000008", "theo", hash_password("theo-pass"), "Theo Williams", "theo.williams@example.com", "Basic"),
                (9, "10000009", "lena", hash_password("lena-pass"), "Lena Ortiz", "lena.ortiz@example.com", "Gold"),
                (10, "10000010", "marcus", hash_password("marcus-pass"), "Marcus Kim", "marcus.kim@example.com", "Silver"),
            ],
        )
        conn.executemany(
            "UPDATE users SET username = ?, password_hash = ? WHERE id = ?",
            [
                ("avery", hash_password("avery-pass"), 1),
                ("mina", hash_password("mina-pass"), 2),
                ("jordan", hash_password("jordan-pass"), 3),
                ("sam", hash_password("sam-pass"), 4),
                ("nora", hash_password("nora-pass"), 5),
                ("eli", hash_password("eli-pass"), 6),
                ("priya", hash_password("priya-pass"), 7),
                ("theo", hash_password("theo-pass"), 8),
                ("lena", hash_password("lena-pass"), 9),
                ("marcus", hash_password("marcus-pass"), 10),
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO knowledge_documents
                (id, category, title, content, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "faq",
                    "Carry-on and checked baggage allowance",
                    "Every customer may bring one carry-on bag and one personal item into the cabin. A checked bag up to 50 pounds is included on standard and premium fares. Bags over 50 pounds may incur an overweight fee.",
                    "Airline FAQ: Baggage",
                ),
                (
                    2,
                    "policy",
                    "Overweight and oversized bag fees",
                    "Checked bags weighing 51 to 70 pounds incur a 75 dollar overweight fee. Bags weighing 71 to 100 pounds incur a 150 dollar overweight fee. Bags larger than 62 linear inches may incur an oversized bag fee.",
                    "Customer Service Policy Manual: Baggage Fees",
                ),
                (
                    3,
                    "faq",
                    "Seat map and aircraft layout",
                    "Most demo flights use a 120 seat aircraft layout with 22 business class seats and 98 economy seats. Exit rows are rows 4 and 16. Rows 5 through 8 are Economy Plus with extra legroom.",
                    "Airline FAQ: Seats",
                ),
                (
                    4,
                    "policy",
                    "Seat changes",
                    "Customers with confirmed bookings may change seats when seats are available. Seat changes are not available for cancelled bookings. Premium seat selections may require a fare difference or loyalty benefit.",
                    "Customer Service Policy Manual: Seats",
                ),
                (
                    5,
                    "faq",
                    "Inflight Wi-Fi",
                    "Inflight Wi-Fi is free on equipped aircraft. Customers can join the Airline-Wifi network after boarding. Streaming quality may vary by aircraft and route.",
                    "Airline FAQ: Wi-Fi",
                ),
                (
                    6,
                    "policy",
                    "Cancellation window",
                    "Customers may cancel a confirmed booking before departure. Refund eligibility depends on fare type, loyalty tier, and whether the flight was disrupted by the airline.",
                    "Customer Service Policy Manual: Cancellations",
                ),
                (
                    7,
                    "policy",
                    "Flight disruption rebooking",
                    "When a flight is cancelled by the airline, customers may be rebooked on the next available flight at no additional charge. Customers may also request travel credit or a refund when eligible.",
                    "Customer Service Policy Manual: Irregular Operations",
                ),
                (
                    8,
                    "faq",
                    "Check-in timing",
                    "Online check-in opens 24 hours before scheduled departure and closes 45 minutes before domestic departures or 60 minutes before international departures.",
                    "Airline FAQ: Check-in",
                ),
                (
                    9,
                    "policy",
                    "Boarding groups",
                    "Boarding begins with customers who need assistance, followed by Platinum, Gold, Silver, premium cabin, and general boarding groups. Gate agents may adjust boarding order during disruptions.",
                    "Customer Service Policy Manual: Boarding",
                ),
                (
                    10,
                    "faq",
                    "Pets in cabin",
                    "Small cats and dogs may travel in cabin on eligible flights when they remain in an approved carrier under the seat. Pet reservations are limited by aircraft and route.",
                    "Airline FAQ: Pets",
                ),
                (
                    11,
                    "policy",
                    "Unaccompanied minors",
                    "Children traveling alone may require unaccompanied minor service depending on age and itinerary. The service is not available on some connecting or international itineraries.",
                    "Customer Service Policy Manual: Special Assistance",
                ),
                (
                    12,
                    "faq",
                    "Loyalty benefits",
                    "Gold and Platinum members may receive priority boarding, preferred seats when available, and additional baggage benefits. Benefits vary by route and fare.",
                    "Airline FAQ: Loyalty",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO flights
                (id, flight_number, origin, destination, departure_time, arrival_time, status, gate, aircraft, seat_capacity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "FLT-123", "SFO", "JFK", "2026-04-20T08:30:00", "2026-04-20T17:05:00", "on time", "A10", "Airbus A220", 120),
                (2, "FLT-476", "SEA", "ORD", "2026-04-21T12:15:00", "2026-04-21T18:20:00", "delayed 35 minutes", "C7", "Boeing 737", 156),
                (3, "FLT-789", "LAX", "DEN", "2026-04-22T09:45:00", "2026-04-22T13:05:00", "boarding", "B4", "Airbus A320", 150),
                (4, "FLT-245", "ATL", "MIA", "2026-04-23T16:10:00", "2026-04-23T18:05:00", "on time", "D12", "Embraer 175", 76),
                (5, "FLT-302", "BOS", "SFO", "2026-04-24T07:20:00", "2026-04-24T10:55:00", "on time", "E3", "Boeing 757", 176),
                (6, "FLT-618", "JFK", "LHR", "2026-04-25T19:40:00", "2026-04-26T07:35:00", "scheduled", "A2", "Boeing 787", 248),
                (7, "FLT-904", "DFW", "PHX", "2026-04-26T14:05:00", "2026-04-26T15:45:00", "cancelled", "C12", "Airbus A319", 124),
                (8, "FLT-551", "MIA", "SJU", "2026-04-27T11:30:00", "2026-04-27T14:05:00", "on time", "H6", "Airbus A321", 190),
                (9, "FLT-842", "ORD", "SEA", "2026-04-28T06:15:00", "2026-04-28T08:55:00", "scheduled", "K18", "Boeing 737 MAX 8", 172),
                (10, "FLT-330", "DEN", "SFO", "2026-04-29T17:25:00", "2026-04-29T19:10:00", "on time", "B9", "Airbus A320", 150),
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO bookings
                (id, confirmation_number, user_id, flight_id, seat_number, status, checked_bags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "LL0EZ6", 1, 1, "12A", "confirmed", 1),
                (2, "MN4Q8K", 2, 2, "23C", "confirmed", 0),
                (3, "JP9R2D", 3, 3, "4F", "confirmed", 2),
                (4, "SR7B5N", 4, 4, "16D", "cancelled", 1),
                (5, "AV2NYC", 1, 2, "8B", "confirmed", 0),
                (6, "NB5SFO", 5, 5, "14C", "confirmed", 1),
                (7, "NB8LHR", 5, 6, "3A", "checked_in", 2),
                (8, "EM7PHX", 6, 7, "21D", "cancelled", 0),
                (9, "EM1SEA", 6, 9, "18F", "confirmed", 1),
                (10, "PS3SJU", 7, 8, "2D", "checked_in", 1),
                (11, "PS6DEN", 7, 10, "10A", "confirmed", 0),
                (12, "TW4MIA", 8, 4, "19B", "standby", 0),
                (13, "TW2JFK", 8, 1, "27E", "confirmed", 1),
                (14, "LO9ORD", 9, 2, "6C", "confirmed", 2),
                (15, "LO3SFO", 9, 5, "11D", "confirmed", 0),
                (16, "MK8LHR", 10, 6, "20G", "confirmed", 1),
                (17, "MK1SEA", 10, 9, "15A", "checked_in", 1),
                (18, "AS7SJU", 1, 8, "5F", "confirmed", 0),
                (19, "MC2DEN", 2, 10, "22A", "standby", 0),
                (20, "JP4BOS", 3, 5, "7B", "confirmed", 1),
            ],
        )


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, account_number, username, password_hash, full_name, email, loyalty_tier
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
    user = _row_to_dict(row)
    if user is None:
        return None
    expected = user.pop("password_hash")
    if expected is None:
        return None
    if not secrets.compare_digest(expected, hash_password(password)):
        return None
    return user


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, account_number, username, full_name, email, loyalty_tier
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
    return _row_to_dict(row)


def get_booking(confirmation_number: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                b.id AS booking_id,
                b.confirmation_number,
                b.seat_number,
                b.status AS booking_status,
                b.checked_bags,
                u.id AS user_id,
                u.account_number,
                u.username,
                u.full_name AS passenger_name,
                u.email,
                u.loyalty_tier,
                f.id AS flight_id,
                f.flight_number,
                f.origin,
                f.destination,
                f.departure_time,
                f.arrival_time,
                f.status AS flight_status,
                f.gate,
                f.aircraft,
                f.seat_capacity
            FROM bookings b
            JOIN users u ON u.id = b.user_id
            JOIN flights f ON f.id = b.flight_id
            WHERE b.confirmation_number = ?
            """,
            (confirmation_number.upper(),),
        ).fetchone()
    return _row_to_dict(row)


def get_default_booking() -> dict[str, Any]:
    confirmation_number = os.getenv("DEMO_CONFIRMATION_NUMBER", "LL0EZ6")
    booking = get_booking(confirmation_number)
    if booking is None:
        raise RuntimeError(f"Seed booking not found: {confirmation_number}")
    return booking


def get_default_booking_for_username(username: str) -> dict[str, Any]:
    requested_confirmation = os.getenv("DEMO_CONFIRMATION_NUMBER")
    with get_connection() as conn:
        if requested_confirmation:
            row = conn.execute(
                """
                SELECT b.confirmation_number
                FROM bookings b
                JOIN users u ON u.id = b.user_id
                WHERE b.confirmation_number = ? AND u.username = ?
                """,
                (requested_confirmation.upper(), username),
            ).fetchone()
            if row is not None:
                booking = get_booking(row["confirmation_number"])
                if booking is not None:
                    return booking

        row = conn.execute(
            """
            SELECT b.confirmation_number
            FROM bookings b
            JOIN users u ON u.id = b.user_id
            WHERE u.username = ?
            ORDER BY
                CASE b.status
                    WHEN 'confirmed' THEN 0
                    WHEN 'checked_in' THEN 1
                    WHEN 'standby' THEN 2
                    ELSE 3
                END,
                b.id
            LIMIT 1
            """,
            (username,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"No seeded booking found for user: {username}")
    booking = get_booking(row["confirmation_number"])
    if booking is None:
        raise RuntimeError(f"Seed booking not found: {row['confirmation_number']}")
    return booking


def get_bookings_for_account(account_number: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                b.confirmation_number,
                b.seat_number,
                b.status AS booking_status,
                f.flight_number,
                f.origin,
                f.destination,
                f.departure_time,
                f.status AS flight_status,
                f.gate
            FROM bookings b
            JOIN users u ON u.id = b.user_id
            JOIN flights f ON f.id = b.flight_id
            WHERE u.account_number = ?
            ORDER BY f.departure_time
            """,
            (account_number,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_booking_seat(confirmation_number: str, new_seat: str) -> dict[str, Any] | None:
    confirmation_number = confirmation_number.upper()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM bookings WHERE confirmation_number = ?",
            (confirmation_number,),
        ).fetchone()
        if row is None or row["status"] == "cancelled":
            return None
        conn.execute(
            "UPDATE bookings SET seat_number = ? WHERE confirmation_number = ?",
            (new_seat.upper(), confirmation_number),
        )
    return get_booking(confirmation_number)


def cancel_booking(confirmation_number: str) -> dict[str, Any] | None:
    confirmation_number = confirmation_number.upper()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM bookings WHERE confirmation_number = ?",
            (confirmation_number,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE confirmation_number = ?",
            (confirmation_number,),
        )
    return get_booking(confirmation_number)


def get_flight(flight_number: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                flight_number,
                origin,
                destination,
                departure_time,
                arrival_time,
                status AS flight_status,
                gate,
                aircraft,
                seat_capacity
            FROM flights
            WHERE flight_number = ?
            """,
            (flight_number.upper(),),
        ).fetchone()
    return _row_to_dict(row)


def _tokenize(text: str) -> set[str]:
    normalized = text.lower().replace("wi-fi", "wifi")
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "may",
        "my",
        "of",
        "on",
        "or",
        "policy",
        "the",
        "to",
        "what",
        "when",
        "with",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) > 1 and token not in stop_words
    }


def search_knowledge_base(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Return the highest scoring FAQ/policy documents for a query."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, category, title, content, source
            FROM knowledge_documents
            ORDER BY id
            """
        ).fetchall()

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        doc = dict(row)
        title_tokens = _tokenize(doc["title"])
        body_tokens = _tokenize(doc["content"])
        category_tokens = _tokenize(doc["category"])
        score = (
            len(query_tokens & title_tokens) * 3
            + len(query_tokens & body_tokens)
            + len(query_tokens & category_tokens)
        )
        if score > 0:
            doc["score"] = score
            scored.append((score, doc))

    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [doc for _, doc in scored[:limit]]


init_database()
