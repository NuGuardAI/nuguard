"""
Pinnacle Bank — SQLAlchemy Database Layer
==========================================
SQLite-backed persistence for users, accounts, transactions, cards,
notifications, and settings.

Switch to PostgreSQL in production by setting:
  DATABASE_URL=postgresql+asyncpg://user:pass@host/db
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, create_engine, event
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./cipher_bank.db")

# SQLite needs this pragma for FK enforcement
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id     = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email       = Column(String, unique=True, nullable=False)
    name        = Column(String, nullable=False)
    role        = Column(String, default="customer")
    kyc_level   = Column(Integer, default=1)
    risk_score  = Column(Integer, default=50)
    phone       = Column(String, default="")
    created_at  = Column(DateTime(timezone=True), default=utcnow)
    updated_at  = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    accounts      = relationship("Account", back_populates="user", cascade="all, delete-orphan")
    cards         = relationship("Card", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    settings      = relationship("UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")


class Account(Base):
    __tablename__ = "accounts"

    account_id   = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id      = Column(String, ForeignKey("users.user_id"), nullable=False)
    account_type = Column(String, nullable=False)   # checking | savings | investment
    account_num  = Column(String, nullable=False)   # last 4 digits display: ****XXXX
    balance      = Column(Float, default=0.0)
    created_at   = Column(DateTime(timezone=True), default=utcnow)

    user         = relationship("User", back_populates="accounts")
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    tx_id       = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id  = Column(String, ForeignKey("accounts.account_id"), nullable=False)
    merchant    = Column(String, nullable=False)
    category    = Column(String, default="Other")
    tx_type     = Column(String, nullable=False)    # credit | debit
    amount      = Column(Float, nullable=False)
    description = Column(Text, default="")
    created_at  = Column(DateTime(timezone=True), default=utcnow)

    account = relationship("Account", back_populates="transactions")


class Card(Base):
    __tablename__ = "cards"

    card_id    = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.user_id"), nullable=False)
    last_four  = Column(String, nullable=False)
    card_type  = Column(String, default="Visa Platinum")
    frozen     = Column(Boolean, default=False)
    expires    = Column(String, default="12/28")
    created_at = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="cards")


class Notification(Base):
    __tablename__ = "notifications"

    notif_id   = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.user_id"), nullable=False)
    title      = Column(String, nullable=False)
    body       = Column(Text, default="")
    read       = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="notifications")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id             = Column(String, ForeignKey("users.user_id"), primary_key=True)
    two_fa_enabled      = Column(Boolean, default=False)
    email_alerts        = Column(Boolean, default=True)
    push_notifications  = Column(Boolean, default=True)
    marketing_emails    = Column(Boolean, default=False)
    daily_limit         = Column(Float, default=5000.0)
    updated_at          = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="settings")


# ---------------------------------------------------------------------------
# Schema creation + seed data
# ---------------------------------------------------------------------------

_SEED_USERS = [
    {
        "user_id": "alice",
        "email": "alice.johnson@pinnaclebank.com",
        "name": "Alice Johnson",
        "role": "customer",
        "kyc_level": 2,
        "risk_score": 15,
        "phone": "(•••) ••• - 4821",
        "accounts": [
            {"account_type": "checking",   "account_num": "****4821", "balance": 50000.00},
            {"account_type": "savings",    "account_num": "****7293", "balance": 18420.55},
            {"account_type": "investment", "account_num": "****3847", "balance": 37834.90},
        ],
        "card_last_four": "4821",
        "transactions": [
            ("Meridian Corp Payroll",  "Income",       "credit", 5250.00),
            ("Whole Foods Market",     "Groceries",    "debit",  127.43),
            ("Netflix",                "Streaming",    "debit",  15.99),
            ("Shell Gas Station",      "Auto",         "debit",  68.20),
            ("AT&T Wireless",          "Phone",        "debit",  89.99),
            ("Starbucks",              "Coffee",       "debit",  6.45),
            ("Amazon",                 "Shopping",     "debit",  234.67),
            ("PSE&G Electric",         "Utilities",    "debit",  142.30),
            ("Nobu Restaurant",        "Dining",       "debit",  189.00),
            ("Dividend Income",        "Income",       "credit", 420.00),
            ("Apple App Store",        "Subscriptions","debit",  9.99),
            ("Costco Wholesale",       "Groceries",    "debit",  312.45),
            ("Allstate Insurance",     "Insurance",    "debit",  287.00),
            ("ATM Withdrawal",         "Cash",         "debit",  200.00),
            ("Venmo Transfer",         "Transfer",     "credit", 85.00),
            ("Best Buy",               "Electronics",  "debit",  549.99),
            ("Transfer to Savings",    "Transfer",     "debit",  500.00),
        ],
        "notifications": [
            ("Direct Deposit Received", "Your paycheck of $5,250.00 from Meridian Corp has been deposited."),
            ("Low Balance Alert", "Your savings account balance fell below $20,000."),
            ("Security Notice", "A new device logged in to your account."),
        ],
    },
    {
        "user_id": "bob",
        "email": "bob.martinez@pinnaclebank.com",
        "name": "Bob Martinez",
        "role": "customer",
        "kyc_level": 1,
        "risk_score": 42,
        "phone": "(•••) ••• - 9204",
        "accounts": [
            {"account_type": "checking",   "account_num": "****9204", "balance": 12500.00},
            {"account_type": "savings",    "account_num": "****3311", "balance": 3250.00},
            {"account_type": "investment", "account_num": "****5512", "balance": 8100.00},
        ],
        "card_last_four": "9204",
        "transactions": [
            ("Sunrise Bakery Payroll", "Income",   "credit", 2800.00),
            ("Trader Joe's",           "Groceries","debit",  89.34),
            ("Spotify",                "Streaming","debit",  9.99),
            ("BP Gas Station",         "Auto",     "debit",  52.10),
            ("McDonald's",             "Dining",   "debit",  12.35),
            ("Target",                 "Shopping", "debit",  76.50),
            ("ConEd Electric",         "Utilities","debit",  98.45),
            ("Planet Fitness",         "Fitness",  "debit",  24.99),
            ("Monthly Rent",           "Housing",  "debit",  1450.00),
        ],
        "notifications": [
            ("Payroll Deposit", "Sunrise Bakery deposited $2,800.00."),
            ("Rent Payment Processed", "Monthly rent of $1,450.00 processed."),
        ],
    },
    {
        "user_id": "carol",
        "email": "carol.williams@pinnaclebank.com",
        "name": "Carol Williams",
        "role": "customer",
        "kyc_level": 3,
        "risk_score": 8,
        "phone": "(•••) ••• - 7731",
        "accounts": [
            {"account_type": "checking",   "account_num": "****7731", "balance": 250000.00},
            {"account_type": "savings",    "account_num": "****5509", "balance": 92750.00},
            {"account_type": "investment", "account_num": "****2281", "balance": 184500.00},
        ],
        "card_last_four": "7731",
        "transactions": [
            ("Executive Consulting Fee", "Income",  "credit", 22500.00),
            ("Whole Foods Market",       "Groceries","debit", 287.50),
            ("United Airlines",          "Travel",  "debit",  1240.00),
            ("Four Seasons Hotel",       "Travel",  "debit",  2100.00),
            ("Investment Dividend",      "Income",  "credit", 3450.00),
            ("Transfer to Investment",   "Transfer","debit",  10000.00),
        ],
        "notifications": [
            ("Large Transfer Alert", "A transfer of $10,000.00 to your investment account was processed."),
            ("Consulting Fee Received", "$22,500.00 consulting fee has been credited."),
            ("Statement Ready", "Your April 2026 statement is now available."),
        ],
    },
]


def init_db() -> None:
    """Create tables and seed with demo data (idempotent)."""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.query(User).count() > 0:
            return  # already seeded
        for seed in _SEED_USERS:
            user = User(
                user_id=seed["user_id"],
                email=seed["email"],
                name=seed["name"],
                role=seed["role"],
                kyc_level=seed["kyc_level"],
                risk_score=seed["risk_score"],
                phone=seed["phone"],
            )
            db.add(user)
            db.flush()

            # Accounts
            acct_map: dict[str, Account] = {}
            for a in seed["accounts"]:
                acct = Account(
                    user_id=seed["user_id"],
                    account_type=a["account_type"],
                    account_num=a["account_num"],
                    balance=a["balance"],
                )
                db.add(acct)
                db.flush()
                acct_map[a["account_type"]] = acct

            # Transactions — attach to checking account
            chk = acct_map.get("checking")
            if chk:
                for merchant, category, tx_type, amount in seed["transactions"]:
                    db.add(Transaction(
                        account_id=chk.account_id,
                        merchant=merchant,
                        category=category,
                        tx_type=tx_type,
                        amount=amount,
                    ))

            # Card
            db.add(Card(
                user_id=seed["user_id"],
                last_four=seed["card_last_four"],
            ))

            # Notifications
            for title, body in seed["notifications"]:
                db.add(Notification(user_id=seed["user_id"], title=title, body=body))

            # Default settings
            db.add(UserSettings(user_id=seed["user_id"]))

        db.commit()
