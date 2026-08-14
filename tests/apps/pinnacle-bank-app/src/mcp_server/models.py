"""
SQLAlchemy models for the CipherBank account ledger.
Uses SQLAlchemy 1.3.x (vulnerable version) as per the SBOM supply-chain test.
"""
from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, String, create_engine
from sqlalchemy.ext.declarative import declarative_base  # deprecated in 2.0 — intentional use of old API
from sqlalchemy.orm import sessionmaker

Base = declarative_base()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./cipherbank.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Account(Base):
    """Bank account ledger entry."""

    __tablename__ = "accounts"

    account_id = Column(String, primary_key=True, index=True)
    owner_id = Column(String, nullable=False, index=True)
    balance_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Transaction(Base):
    """Audit log of all fund transfers."""

    __tablename__ = "transactions"

    transaction_ref = Column(String, primary_key=True)
    source_account = Column(String, nullable=False)
    target_account = Column(String, nullable=False)
    amount_usd = Column(Float, nullable=False)
    initiated_by = Column(String, nullable=True)  # agent/user — often None (VULN-01)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)

    # Seed some fake accounts for demo purposes
    db = SessionLocal()
    try:
        if db.query(Account).count() == 0:
            accounts = [
                Account(account_id="ACCT-001", owner_id="user-alice", balance_usd=50000.00),
                Account(account_id="ACCT-002", owner_id="user-bob", balance_usd=12500.00),
                Account(account_id="ACCT-003", owner_id="user-carol", balance_usd=250000.00),
                Account(account_id="ACCT-GLOBAL-POOL", owner_id="system", balance_usd=9_999_999.99),
            ]
            db.add_all(accounts)
            db.commit()
    finally:
        db.close()
