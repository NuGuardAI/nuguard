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
                phone TEXT,
                address TEXT,
                passport_number TEXT,
                nationality TEXT,
                date_of_birth TEXT,
                loyalty_tier TEXT NOT NULL,
                loyalty_miles INTEGER NOT NULL DEFAULT 0,
                travel_credit_usd INTEGER NOT NULL DEFAULT 0,
                seat_preference TEXT,
                meal_preference TEXT,
                special_assistance TEXT,
                tsa_precheck TEXT,
                known_traveler_number TEXT
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
                seat_capacity INTEGER NOT NULL,
                meal_service TEXT,
                wifi_available INTEGER NOT NULL DEFAULT 1,
                distance_miles INTEGER
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY,
                confirmation_number TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id),
                flight_id INTEGER NOT NULL REFERENCES flights(id),
                seat_number TEXT NOT NULL,
                cabin_class TEXT NOT NULL DEFAULT 'Economy',
                status TEXT NOT NULL,
                checked_bags INTEGER NOT NULL DEFAULT 0,
                meal_choice TEXT,
                upgrade_status TEXT,
                special_request TEXT,
                fare_type TEXT,
                ticket_price_usd INTEGER
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

        # Add new columns to existing tables if they don't exist (safe migration)
        existing_user_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        new_user_cols = {
            "username": "TEXT", "password_hash": "TEXT", "phone": "TEXT",
            "address": "TEXT", "passport_number": "TEXT", "nationality": "TEXT",
            "date_of_birth": "TEXT", "loyalty_miles": "INTEGER NOT NULL DEFAULT 0",
            "travel_credit_usd": "INTEGER NOT NULL DEFAULT 0",
            "seat_preference": "TEXT", "meal_preference": "TEXT",
            "special_assistance": "TEXT", "tsa_precheck": "TEXT",
            "known_traveler_number": "TEXT",
        }
        for col, col_type in new_user_cols.items():
            if col not in existing_user_cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")

        existing_flight_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(flights)").fetchall()
        }
        for col, col_type in [
            ("meal_service", "TEXT"),
            ("wifi_available", "INTEGER NOT NULL DEFAULT 1"),
            ("distance_miles", "INTEGER"),
        ]:
            if col not in existing_flight_cols:
                conn.execute(f"ALTER TABLE flights ADD COLUMN {col} {col_type}")

        existing_booking_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(bookings)").fetchall()
        }
        for col, col_type in [
            ("cabin_class", "TEXT NOT NULL DEFAULT 'Economy'"),
            ("meal_choice", "TEXT"),
            ("upgrade_status", "TEXT"),
            ("special_request", "TEXT"),
            ("fare_type", "TEXT"),
            ("ticket_price_usd", "INTEGER"),
        ]:
            if col not in existing_booking_cols:
                conn.execute(f"ALTER TABLE bookings ADD COLUMN {col} {col_type}")

        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")

        # ── 20 users with rich, diverse profiles ──────────────────────────────
        conn.executemany(
            """
            INSERT OR IGNORE INTO users
                (id, account_number, username, password_hash, full_name, email,
                 phone, address, passport_number, nationality, date_of_birth,
                 loyalty_tier, loyalty_miles, travel_credit_usd,
                 seat_preference, meal_preference, special_assistance,
                 tsa_precheck, known_traveler_number)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                # id, acct, user, pw, name, email, phone, address, passport, nationality, dob
                # tier, miles, credit, seat_pref, meal_pref, special_assist, tsa, ktn
                (1,  "10000001", "avery",   hash_password("avery-pass"),
                 "Avery Stone",       "avery.stone@example.com",
                 "+1-415-555-0101", "742 Evergreen Terrace, San Francisco, CA 94102",
                 "A12345678", "USA", "1988-03-15",
                 "Gold", 48200, 75,
                 "Window", "Standard", None, "TSA PreCheck", "KTN-AV8001"),

                (2,  "10000002", "mina",    hash_password("mina-pass"),
                 "Mina Chen",         "mina.chen@example.com",
                 "+1-206-555-0102", "12 Pike Place, Seattle, WA 98101",
                 "B98765432", "USA", "1994-07-22",
                 "Silver", 22500, 0,
                 "Aisle", "Vegetarian", None, None, None),

                (3,  "10000003", "jordan",  hash_password("jordan-pass"),
                 "Jordan Patel",      "jordan.patel@example.com",
                 "+1-312-555-0103", "444 N Michigan Ave, Chicago, IL 60611",
                 "C55512300", "USA", "1979-11-30",
                 "Platinum", 195400, 250,
                 "Aisle", "Hindu Vegetarian", None, "TSA PreCheck", "KTN-JP7799"),

                (4,  "10000004", "sam",     hash_password("sam-pass"),
                 "Sam Rivera",        "sam.rivera@example.com",
                 "+1-305-555-0104", "88 Brickell Ave, Miami, FL 33131",
                 "D22244466", "USA", "2001-05-09",
                 "Basic", 3100, 0,
                 "No preference", "Standard", None, None, None),

                (5,  "10000005", "nora",    hash_password("nora-pass"),
                 "Nora Brooks",       "nora.brooks@example.com",
                 "+1-617-555-0105", "1 Charles St S, Boston, MA 02116",
                 "E77700123", "USA", "1985-09-18",
                 "Gold", 61800, 100,
                 "Window", "Gluten Free", "Wheelchair assistance requested", "TSA PreCheck", "KTN-NB5050"),

                (6,  "10000006", "eli",     hash_password("eli-pass"),
                 "Eli Morgan",        "eli.morgan@example.com",
                 "+1-214-555-0106", "500 Commerce St, Dallas, TX 75201",
                 "F33366699", "USA", "1992-01-25",
                 "Silver", 18700, 25,
                 "Aisle", "Standard", None, None, None),

                (7,  "10000007", "priya",   hash_password("priya-pass"),
                 "Priya Shah",        "priya.shah@example.com",
                 "+1-212-555-0107", "350 5th Ave, New York, NY 10118",
                 "G11122233", "USA", "1983-06-12",
                 "Platinum", 287600, 500,
                 "Aisle", "Vegan", None, "Global Entry", "GE-PR9900"),

                (8,  "10000008", "theo",    hash_password("theo-pass"),
                 "Theo Williams",     "theo.williams@example.com",
                 "+1-602-555-0108", "200 E Van Buren St, Phoenix, AZ 85004",
                 "H99988877", "USA", "1997-12-03",
                 "Basic", 1500, 0,
                 "No preference", "Standard", None, None, None),

                (9,  "10000009", "lena",    hash_password("lena-pass"),
                 "Lena Ortiz",        "lena.ortiz@example.com",
                 "+1-720-555-0109", "1600 Glenarm Place, Denver, CO 80202",
                 "I44455566", "USA", "1990-04-07",
                 "Gold", 52300, 150,
                 "Window", "Kosher", None, "TSA PreCheck", "KTN-LO3344"),

                (10, "10000010", "marcus",  hash_password("marcus-pass"),
                 "Marcus Kim",        "marcus.kim@example.com",
                 "+1-503-555-0110", "1 SW Columbia St, Portland, OR 97204",
                 "J00011122", "USA", "1975-08-19",
                 "Silver", 31200, 50,
                 "Aisle", "Standard", None, None, None),

                (11, "10000011", "sofia",   hash_password("sofia-pass"),
                 "Sofia Nguyen",      "sofia.nguyen@example.com",
                 "+1-424-555-0111", "6200 Hollywood Blvd, Los Angeles, CA 90028",
                 "K55544433", "USA", "1996-02-28",
                 "Basic", 8400, 0,
                 "Window", "Standard", None, None, None),

                (12, "10000012", "alex",    hash_password("alex-pass"),
                 "Alex Reyes",        "alex.reyes@example.com",
                 "+1-404-555-0112", "191 Peachtree St NE, Atlanta, GA 30303",
                 "L77788899", "USA", "1981-10-11",
                 "Gold", 73900, 200,
                 "Aisle", "Diabetic Meal", None, "TSA PreCheck", "KTN-AR6611"),

                (13, "10000013", "camille", hash_password("camille-pass"),
                 "Camille Dubois",    "camille.dubois@example.com",
                 "+33-6-12-34-56-78", "42 Rue de Rivoli, Paris 75001, France",
                 "03AB12345", "France", "1987-03-14",
                 "Platinum", 156800, 300,
                 "Aisle", "Standard", None, None, None),

                (14, "10000014", "derek",   hash_password("derek-pass"),
                 "Derek Okonkwo",     "derek.okonkwo@example.com",
                 "+44-7700-900123",   "221B Baker St, London NW1 6XE, UK",
                 "GB123456789", "UK", "1977-07-04",
                 "Platinum", 224100, 400,
                 "Aisle", "Halal", None, None, None),

                (15, "10000015", "yuki",    hash_password("yuki-pass"),
                 "Yuki Tanaka",       "yuki.tanaka@example.com",
                 "+81-90-1234-5678",  "1-1-1 Shinjuku, Tokyo 160-0022, Japan",
                 "TK9876543", "Japan", "1993-09-05",
                 "Silver", 27600, 0,
                 "Window", "Japanese Meal", None, None, None),

                (16, "10000016", "isadora", hash_password("isadora-pass"),
                 "Isadora Santos",    "isadora.santos@example.com",
                 "+55-11-98765-4321", "Av. Paulista 1000, São Paulo 01310-100, Brazil",
                 "BR44455566", "Brazil", "1989-12-20",
                 "Gold", 44700, 75,
                 "Aisle", "Standard", None, None, None),

                (17, "10000017", "raj",     hash_password("raj-pass"),
                 "Raj Krishnamurthy", "raj.krishnamurthy@example.com",
                 "+1-408-555-0117", "3000 Sand Hill Rd, Menlo Park, CA 94025",
                 "M12312312", "USA", "1982-05-30",
                 "Platinum", 341200, 600,
                 "Aisle", "Hindu Vegetarian", None, "Global Entry", "GE-RK8877"),

                (18, "10000018", "grace",   hash_password("grace-pass"),
                 "Grace O'Sullivan",  "grace.osullivan@example.com",
                 "+353-86-123-4567",  "St. Stephen's Green 10, Dublin D02 DE24, Ireland",
                 "IE654321",   "Ireland", "1991-08-17",
                 "Basic", 6200, 0,
                 "Window", "Standard", None, None, None),

                (19, "10000019", "felix",   hash_password("felix-pass"),
                 "Felix Bauer",       "felix.bauer@example.com",
                 "+49-30-12345678",   "Unter den Linden 77, Berlin 10117, Germany",
                 "DE99988877", "Germany", "1984-04-02",
                 "Silver", 39100, 50,
                 "Aisle", "Standard", None, None, None),

                (20, "10000020", "claire",  hash_password("claire-pass"),
                 "Claire Fontaine",   "claire.fontaine@example.com",
                 "+1-514-555-0120", "1000 Rue de la Gauchetière, Montréal, QC H3B 4W5",
                 "CA11122233", "Canada", "1995-11-08",
                 "Gold", 55500, 125,
                 "Window", "Vegan", None, "TSA PreCheck", "KTN-CF2020"),
            ],
        )

        # Idempotent password/username refresh
        conn.executemany(
            "UPDATE users SET username = ?, password_hash = ? WHERE id = ?",
            [
                ("avery",   hash_password("avery-pass"),   1),
                ("mina",    hash_password("mina-pass"),    2),
                ("jordan",  hash_password("jordan-pass"),  3),
                ("sam",     hash_password("sam-pass"),     4),
                ("nora",    hash_password("nora-pass"),    5),
                ("eli",     hash_password("eli-pass"),     6),
                ("priya",   hash_password("priya-pass"),   7),
                ("theo",    hash_password("theo-pass"),    8),
                ("lena",    hash_password("lena-pass"),    9),
                ("marcus",  hash_password("marcus-pass"),  10),
                ("sofia",   hash_password("sofia-pass"),   11),
                ("alex",    hash_password("alex-pass"),    12),
                ("camille", hash_password("camille-pass"), 13),
                ("derek",   hash_password("derek-pass"),   14),
                ("yuki",    hash_password("yuki-pass"),    15),
                ("isadora", hash_password("isadora-pass"), 16),
                ("raj",     hash_password("raj-pass"),     17),
                ("grace",   hash_password("grace-pass"),   18),
                ("felix",   hash_password("felix-pass"),   19),
                ("claire",  hash_password("claire-pass"),  20),
            ],
        )

        # ── 20 flights (domestic + international) ─────────────────────────────
        conn.executemany(
            """
            INSERT OR IGNORE INTO flights
                (id, flight_number, origin, destination, departure_time, arrival_time,
                 status, gate, aircraft, seat_capacity, meal_service, wifi_available, distance_miles)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (1,  "FLT-123", "SFO", "JFK", "2026-04-20T08:30:00", "2026-04-20T17:05:00",
                 "on time",         "A10",  "Airbus A220",        120, "Full meal service",          1, 2572),
                (2,  "FLT-476", "SEA", "ORD", "2026-04-21T12:15:00", "2026-04-21T18:20:00",
                 "delayed 35 minutes", "C7", "Boeing 737",        156, "Snacks and beverages",       1, 1721),
                (3,  "FLT-789", "LAX", "DEN", "2026-04-22T09:45:00", "2026-04-22T13:05:00",
                 "boarding",        "B4",  "Airbus A320",        150, "Buy on board",               1, 862),
                (4,  "FLT-245", "ATL", "MIA", "2026-04-23T16:10:00", "2026-04-23T18:05:00",
                 "on time",         "D12", "Embraer 175",         76, "Snacks and beverages",       0, 662),
                (5,  "FLT-302", "BOS", "SFO", "2026-04-24T07:20:00", "2026-04-24T10:55:00",
                 "on time",         "E3",  "Boeing 757",         176, "Full meal service",          1, 2704),
                (6,  "FLT-618", "JFK", "LHR", "2026-04-25T19:40:00", "2026-04-26T07:35:00",
                 "scheduled",       "A2",  "Boeing 787-9",       248, "Dinner and breakfast",       1, 3451),
                (7,  "FLT-904", "DFW", "PHX", "2026-04-26T14:05:00", "2026-04-26T15:45:00",
                 "cancelled",       "C12", "Airbus A319",        124, "Snacks and beverages",       0, 868),
                (8,  "FLT-551", "MIA", "SJU", "2026-04-27T11:30:00", "2026-04-27T14:05:00",
                 "on time",         "H6",  "Airbus A321",        190, "Snacks and beverages",       1, 1035),
                (9,  "FLT-842", "ORD", "SEA", "2026-04-28T06:15:00", "2026-04-28T08:55:00",
                 "scheduled",       "K18", "Boeing 737 MAX 8",   172, "Buy on board",               1, 1721),
                (10, "FLT-330", "DEN", "SFO", "2026-04-29T17:25:00", "2026-04-29T19:10:00",
                 "on time",         "B9",  "Airbus A320",        150, "Snacks and beverages",       1, 955),
                (11, "FLT-115", "LAX", "NRT", "2026-04-30T13:55:00", "2026-05-01T18:20:00",
                 "on time",         "T4-B6", "Boeing 787-10",    296, "Two meals and snack",        1, 5451),
                (12, "FLT-720", "JFK", "CDG", "2026-05-01T21:30:00", "2026-05-02T11:05:00",
                 "scheduled",       "B22", "Airbus A330-300",    277, "Dinner and breakfast",       1, 3627),
                (13, "FLT-403", "SFO", "HNL", "2026-05-02T10:20:00", "2026-05-02T14:40:00",
                 "on time",         "F12", "Airbus A321XLR",     180, "Full meal service",          1, 2397),
                (14, "FLT-655", "ORD", "LHR", "2026-05-03T17:15:00", "2026-05-04T07:30:00",
                 "delayed 85 minutes", "K2", "Boeing 777-200ER", 350, "Dinner and breakfast",       1, 3955),
                (15, "FLT-218", "MCO", "BOS", "2026-05-04T08:00:00", "2026-05-04T11:45:00",
                 "on time",         "G3",  "Boeing 737-800",     162, "Buy on board",               1, 1251),
                (16, "FLT-511", "SFO", "CDG", "2026-05-05T15:30:00", "2026-05-06T11:45:00",
                 "scheduled",       "A14", "Boeing 777-300ER",   396, "Dinner and breakfast",       1, 5567),
                (17, "FLT-038", "DFW", "GRU", "2026-05-06T22:00:00", "2026-05-07T08:40:00",
                 "scheduled",       "D5",  "Boeing 787-8",       210, "Dinner and breakfast",       1, 5135),
                (18, "FLT-271", "SEA", "NRT", "2026-05-07T11:45:00", "2026-05-08T15:20:00",
                 "on time",         "S10", "Boeing 777-200LR",   310, "Two meals and snack",        1, 4800),
                (19, "FLT-609", "BOS", "LHR", "2026-05-08T18:20:00", "2026-05-09T06:05:00",
                 "scheduled",       "C4",  "Airbus A330-200",    252, "Dinner and breakfast",       1, 3254),
                (20, "FLT-180", "LAX", "SYD", "2026-05-09T23:55:00", "2026-05-11T09:30:00",
                 "scheduled",       "B3",  "Airbus A380",        496, "Two meals, two snacks",      1, 7488),
            ],
        )

        # ── 40 bookings across all 20 users ───────────────────────────────────
        conn.executemany(
            """
            INSERT OR IGNORE INTO bookings
                (id, confirmation_number, user_id, flight_id, seat_number,
                 cabin_class, status, checked_bags, meal_choice,
                 upgrade_status, special_request, fare_type, ticket_price_usd)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                # avery (1) — Gold, window preference, 3 bookings
                (1,  "LL0EZ6", 1, 1,  "12A", "Economy", "confirmed",  1, "Standard",        None,            None,                    "Flex",        389),
                (2,  "AV2NYC", 1, 2,  "8B",  "Economy", "confirmed",  0, "Standard",        None,            None,                    "Standard",    215),
                (3,  "AS7SJU", 1, 8,  "5F",  "Economy", "confirmed",  0, "Seafood",         "Upgrade requested", None,                "Flex",        310),

                # mina (2) — Silver, aisle, vegetarian
                (4,  "MN4Q8K", 2, 2,  "23C", "Economy", "confirmed",  0, "Vegetarian",      None,            None,                    "Standard",    215),
                (5,  "MC2DEN", 2, 10, "22A", "Economy", "standby",    0, "Standard",        None,            None,                    "Basic",       149),
                (6,  "MK8LHR", 2, 6,  "38D", "Economy", "confirmed",  1, "Vegetarian",      None,            None,                    "Standard",    680),

                # jordan (3) — Platinum, aisle, Hindu veg, frequent flier
                (7,  "JP9R2D", 3, 3,  "4F",  "Business","confirmed",  2, "Hindu Vegetarian","Confirmed upgrade", "Extra leg room requested", "Business", 2450),
                (8,  "JP4BOS", 3, 5,  "7B",  "Economy", "confirmed",  1, "Hindu Vegetarian",None,            None,                    "Flex",        445),
                (9,  "JP1NRT", 3, 11, "2A",  "Business","confirmed",  2, "Hindu Vegetarian","Confirmed upgrade", None,               "Business",    4800),

                # sam (4) — Basic, no preference
                (10, "SR7B5N", 4, 4,  "16D", "Economy", "cancelled",  1, "Standard",        None,            None,                    "Basic",       129),
                (11, "SA3ORD", 4, 9,  "31E", "Economy", "confirmed",  0, "Standard",        None,            None,                    "Basic",       175),

                # nora (5) — Gold, window, gluten free, wheelchair
                (12, "NB5SFO", 5, 5,  "14C", "Economy", "confirmed",  1, "Gluten Free",     None,            "Wheelchair assistance", "Flex",        445),
                (13, "NB8LHR", 5, 6,  "3A",  "Economy", "checked_in", 2, "Gluten Free",     "Upgrade requested", "Wheelchair assistance at destination", "Flex", 695),
                (14, "NB2HNL", 5, 13, "22A", "Economy", "confirmed",  1, "Gluten Free",     None,            "Wheelchair assistance", "Standard",    580),

                # eli (6) — Silver, aisle
                (15, "EM7PHX", 6, 7,  "21D", "Economy", "cancelled",  0, "Standard",        None,            None,                    "Basic",       129),
                (16, "EM1SEA", 6, 9,  "18F", "Economy", "confirmed",  1, "Standard",        None,            None,                    "Standard",    215),

                # priya (7) — Platinum, aisle, vegan
                (17, "PS3SJU", 7, 8,  "2D",  "First",   "checked_in", 1, "Vegan",           "Confirmed upgrade", None,               "First",       2200),
                (18, "PS6DEN", 7, 10, "10A", "Business","confirmed",  0, "Vegan",           "Confirmed upgrade", None,               "Business",    950),
                (19, "PS9CDG", 7, 12, "1A",  "First",   "confirmed",  2, "Vegan",           "Confirmed upgrade", None,               "First",       6500),

                # theo (8) — Basic, standby and confirmed
                (20, "TW4MIA", 8, 4,  "19B", "Economy", "standby",    0, "Standard",        None,            None,                    "Basic",       129),
                (21, "TW2JFK", 8, 1,  "27E", "Economy", "confirmed",  1, "Standard",        None,            None,                    "Standard",    355),

                # lena (9) — Gold, window, kosher
                (22, "LO9ORD", 9, 2,  "6C",  "Economy", "confirmed",  2, "Kosher",          None,            None,                    "Flex",        215),
                (23, "LO3SFO", 9, 5,  "11D", "Economy", "confirmed",  0, "Kosher",          None,            None,                    "Standard",    445),
                (24, "LO1HNL", 9, 13, "14C", "Economy", "confirmed",  1, "Kosher",          None,            None,                    "Standard",    595),

                # marcus (10) — Silver
                (25, "MK1SEA", 10, 9, "15A", "Economy", "checked_in", 1, "Standard",        None,            None,                    "Flex",        215),
                (26, "MR7SFO", 10, 5, "33B", "Economy", "confirmed",  0, "Standard",        None,            None,                    "Standard",    445),

                # sofia (11) — Basic, window
                (27, "SN4LAX", 11, 3, "19A", "Economy", "confirmed",  0, "Standard",        None,            None,                    "Basic",       159),
                (28, "SN9NRT", 11, 11,"41F", "Economy", "confirmed",  1, "Japanese Meal",   None,            None,                    "Standard",    890),

                # alex (12) — Gold, aisle, diabetic
                (29, "AR2ATL", 12, 4, "8C",  "Economy", "confirmed",  1, "Diabetic Meal",   None,            None,                    "Flex",        155),
                (30, "AR5LHR", 12, 6, "18D", "Economy", "confirmed",  2, "Diabetic Meal",   "Upgrade requested", None,               "Flex",        710),

                # camille (13) — Platinum, Paris-based
                (31, "CD3CDG", 13, 12,"4C",  "Business","confirmed",  2, "Standard",        "Confirmed upgrade", None,               "Business",    3200),
                (32, "CD7SFO", 13, 16,"6D",  "Business","confirmed",  1, "Standard",        "Confirmed upgrade", None,               "Business",    5100),

                # derek (14) — Platinum, London-based, halal
                (33, "DO4JFK", 14, 6, "3C",  "First",   "confirmed",  2, "Halal",           "Confirmed upgrade", "Extra baggage allowance", "First", 7800),
                (34, "DO8ORD", 14, 14,"2A",  "Business","confirmed",  1, "Halal",           "Confirmed upgrade", None,               "Business",    4600),

                # yuki (15) — Silver, Tokyo-based, Japanese meal
                (35, "YT2LAX", 15, 11,"22F", "Economy", "confirmed",  1, "Japanese Meal",   None,            None,                    "Standard",    890),
                (36, "YT5SEA", 15, 18,"27C", "Economy", "confirmed",  0, "Japanese Meal",   None,            None,                    "Standard",    780),

                # isadora (16) — Gold, São Paulo-based
                (37, "IS1DFW", 16, 17,"14B", "Economy", "confirmed",  2, "Standard",        None,            None,                    "Flex",        960),
                (38, "IS8BOS", 16, 5, "29D", "Economy", "confirmed",  1, "Standard",        None,            None,                    "Standard",    480),

                # raj (17) — Platinum, Silicon Valley
                (39, "RK9CDG", 17, 16,"5A",  "First",   "confirmed",  2, "Hindu Vegetarian","Confirmed upgrade", None,               "First",       8900),
                (40, "RK3JFK", 17, 1, "3F",  "Business","confirmed",  1, "Hindu Vegetarian","Confirmed upgrade", None,               "Business",    1850),
            ],
        )

        # ── Knowledge base ─────────────────────────────────────────────────────
        conn.executemany(
            """
            INSERT OR IGNORE INTO knowledge_documents
                (id, category, title, content, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1,  "faq",    "Carry-on and checked baggage allowance",
                 "Every customer may bring one carry-on bag and one personal item into the cabin. "
                 "A checked bag up to 50 pounds is included on standard and premium fares. "
                 "Bags over 50 pounds may incur an overweight fee.",
                 "Airline FAQ: Baggage"),

                (2,  "policy", "Overweight and oversized bag fees",
                 "Checked bags weighing 51 to 70 pounds incur a 75 dollar overweight fee. "
                 "Bags weighing 71 to 100 pounds incur a 150 dollar overweight fee. "
                 "Bags larger than 62 linear inches may incur an oversized bag fee.",
                 "Customer Service Policy Manual: Baggage Fees"),

                (3,  "faq",    "Seat map and aircraft layout",
                 "Most demo flights use a 120 seat aircraft layout with 22 business class seats "
                 "and 98 economy seats. Exit rows are rows 4 and 16. "
                 "Rows 5 through 8 are Economy Plus with extra legroom.",
                 "Airline FAQ: Seats"),

                (4,  "policy", "Seat changes",
                 "Customers with confirmed bookings may change seats when seats are available. "
                 "Seat changes are not available for cancelled bookings. "
                 "Premium seat selections may require a fare difference or loyalty benefit.",
                 "Customer Service Policy Manual: Seats"),

                (5,  "faq",    "Inflight Wi-Fi",
                 "Inflight Wi-Fi is free on equipped aircraft. "
                 "Customers can join the Airline-Wifi network after boarding. "
                 "Streaming quality may vary by aircraft and route.",
                 "Airline FAQ: Wi-Fi"),

                (6,  "policy", "Cancellation window",
                 "Customers may cancel a confirmed booking before departure. "
                 "Refund eligibility depends on fare type, loyalty tier, and whether the flight "
                 "was disrupted by the airline. Flex fares are fully refundable. "
                 "Basic fares are non-refundable but may be converted to travel credit.",
                 "Customer Service Policy Manual: Cancellations"),

                (7,  "policy", "Flight disruption rebooking",
                 "When a flight is cancelled by the airline, customers may be rebooked on the "
                 "next available flight at no additional charge. Customers may also request "
                 "travel credit or a refund when eligible.",
                 "Customer Service Policy Manual: Irregular Operations"),

                (8,  "faq",    "Check-in timing",
                 "Online check-in opens 24 hours before scheduled departure and closes "
                 "45 minutes before domestic departures or 60 minutes before international departures.",
                 "Airline FAQ: Check-in"),

                (9,  "policy", "Boarding groups",
                 "Boarding begins with customers who need assistance, followed by Platinum, Gold, "
                 "Silver, premium cabin, and general boarding groups. "
                 "Gate agents may adjust boarding order during disruptions.",
                 "Customer Service Policy Manual: Boarding"),

                (10, "faq",    "Pets in cabin",
                 "Small cats and dogs may travel in cabin on eligible flights when they remain "
                 "in an approved carrier under the seat. "
                 "Pet reservations are limited by aircraft and route.",
                 "Airline FAQ: Pets"),

                (11, "policy", "Unaccompanied minors",
                 "Children traveling alone may require unaccompanied minor service depending on "
                 "age and itinerary. The service is not available on some connecting or "
                 "international itineraries.",
                 "Customer Service Policy Manual: Special Assistance"),

                (12, "faq",    "Loyalty benefits",
                 "Gold and Platinum members may receive priority boarding, preferred seats when "
                 "available, and additional baggage benefits. Benefits vary by route and fare. "
                 "Platinum members also receive complimentary upgrades when available.",
                 "Airline FAQ: Loyalty"),

                (13, "faq",    "Special meal requests",
                 "Special meals (vegetarian, vegan, kosher, halal, gluten free, diabetic, Hindu "
                 "vegetarian, Japanese) must be requested at least 24 hours before departure. "
                 "Requests can be made via the booking tool or by contacting customer service.",
                 "Airline FAQ: Special Meals"),

                (14, "policy", "Upgrade policy",
                 "Complimentary upgrades are available to Platinum members on eligible fares "
                 "when business or first class seats are available at departure. "
                 "Upgrades can also be purchased at check-in or via the app for any loyalty tier.",
                 "Customer Service Policy Manual: Upgrades"),

                (15, "faq",    "International travel requirements",
                 "Passengers on international flights must carry a valid passport. "
                 "Some destinations require a visa — check entry requirements well in advance. "
                 "Arrive at least 3 hours before international departures for customs and immigration.",
                 "Airline FAQ: International Travel"),

                (16, "policy", "Loyalty miles accrual",
                 "Miles are earned based on distance flown and fare class. Flex and premium "
                 "fares earn 100–150% of base miles. Basic fares earn 25%. "
                 "Miles expire after 18 months of account inactivity.",
                 "Customer Service Policy Manual: Loyalty Program"),

                (17, "faq",    "Baggage tracking",
                 "Checked bags are tagged with a barcode at check-in and tracked throughout the journey. "
                 "Use your booking confirmation number to track bag status on our website or app. "
                 "Delayed bags are delivered to your address within 24 hours at no charge.",
                 "Airline FAQ: Baggage Tracking"),

                (18, "policy", "Wheelchair and mobility assistance",
                 "Customers requiring wheelchair or mobility assistance should notify us at least "
                 "48 hours before departure. Assistance is provided at check-in, through security, "
                 "at the gate, during boarding, and at the destination. "
                 "There is no charge for this service.",
                 "Customer Service Policy Manual: Special Assistance"),

                (19, "faq",    "Travel credit and vouchers",
                 "Travel credits are valid for 12 months from issuance and can be applied to "
                 "any booking. Credits are non-transferable and cannot be redeemed for cash.",
                 "Airline FAQ: Travel Credits"),

                (20, "policy", "Known Traveler Number and TSA PreCheck",
                 "Add your Known Traveler Number (KTN) or Global Entry number to your profile "
                 "to receive TSA PreCheck eligibility on domestic US flights. "
                 "PreCheck lanes allow passengers to keep shoes, laptops, and liquids in bags.",
                 "Customer Service Policy Manual: Security"),
            ],
        )


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, account_number, username, password_hash, full_name, email,
                   loyalty_tier, loyalty_miles, travel_credit_usd,
                   seat_preference, meal_preference, special_assistance,
                   tsa_precheck, known_traveler_number, nationality
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
            SELECT id, account_number, username, full_name, email,
                   loyalty_tier, loyalty_miles, travel_credit_usd,
                   seat_preference, meal_preference, special_assistance,
                   tsa_precheck, known_traveler_number, nationality
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
                b.cabin_class,
                b.status AS booking_status,
                b.checked_bags,
                b.meal_choice,
                b.upgrade_status,
                b.special_request,
                b.fare_type,
                b.ticket_price_usd,
                u.id AS user_id,
                u.account_number,
                u.username,
                u.full_name AS passenger_name,
                u.email,
                u.loyalty_tier,
                u.loyalty_miles,
                u.travel_credit_usd,
                u.meal_preference,
                u.special_assistance,
                u.tsa_precheck,
                f.id AS flight_id,
                f.flight_number,
                f.origin,
                f.destination,
                f.departure_time,
                f.arrival_time,
                f.status AS flight_status,
                f.gate,
                f.aircraft,
                f.seat_capacity,
                f.meal_service,
                f.wifi_available,
                f.distance_miles
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
                    WHEN 'confirmed'  THEN 0
                    WHEN 'checked_in' THEN 1
                    WHEN 'standby'    THEN 2
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
                b.cabin_class,
                b.status AS booking_status,
                b.meal_choice,
                b.upgrade_status,
                b.fare_type,
                b.ticket_price_usd,
                f.flight_number,
                f.origin,
                f.destination,
                f.departure_time,
                f.status AS flight_status,
                f.gate,
                f.aircraft,
                f.distance_miles
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
                seat_capacity,
                meal_service,
                wifi_available,
                distance_miles
            FROM flights
            WHERE flight_number = ?
            """,
            (flight_number.upper(),),
        ).fetchone()
    return _row_to_dict(row)


def _tokenize(text: str) -> set[str]:
    normalized = text.lower().replace("wi-fi", "wifi")
    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do",
        "for", "from", "how", "i", "in", "is", "it", "may", "my",
        "of", "on", "or", "policy", "the", "to", "what", "when", "with",
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
            "SELECT id, category, title, content, source FROM knowledge_documents ORDER BY id"
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
