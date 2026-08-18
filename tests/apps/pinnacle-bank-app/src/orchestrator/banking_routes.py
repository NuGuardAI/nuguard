"""
Pinnacle Bank — Banking REST API Routes
========================================
All endpoints require a valid auth key (same static-key model as /api/chat).
Mounted on main FastAPI app under the /api prefix.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import (
    Account, Card, Notification, Transaction, User, UserSettings, get_db, utcnow
)
from .auth import _USER_STORE  # reuse the same user store for key validation

logger = logging.getLogger("orchestrator.banking")
router = APIRouter(prefix="/api/bank", tags=["banking"])


# ---------------------------------------------------------------------------
# Auth helper — reuse static key model
# ---------------------------------------------------------------------------

def _resolve_user_id(auth_key: str) -> str:
    """Return user_id for a valid auth key, or raise 401."""
    for uid, _ in _USER_STORE.items():
        expected = os.getenv(f"AUTH_KEY_{uid.upper()}", "demo123")
        if expected and expected == auth_key:
            return uid
    raise HTTPException(status_code=401, detail="Invalid or missing auth key.")


def get_current_user_id(authorization: str = Header(default="")) -> str:
    key = authorization.removeprefix("Bearer ").strip()
    if not key:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    return _resolve_user_id(key)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TransferRequest(BaseModel):
    from_account_id: str
    to_account_id: Optional[str] = None   # None = external (Zelle-style)
    amount: float
    memo: str = ""
    recipient_email: Optional[str] = None  # for external

class UpdateSettingsRequest(BaseModel):
    two_fa_enabled: Optional[bool] = None
    email_alerts: Optional[bool] = None
    push_notifications: Optional[bool] = None
    marketing_emails: Optional[bool] = None
    daily_limit: Optional[float] = None
    name: Optional[str] = None
    phone: Optional[str] = None

class FreezeCardRequest(BaseModel):
    frozen: bool


# ---------------------------------------------------------------------------
# User & profile
# ---------------------------------------------------------------------------

@router.get("/me")
def get_profile(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    settings = user.settings
    return {
        "user_id":    user.user_id,
        "name":       user.name,
        "email":      user.email,
        "phone":      user.phone,
        "role":       user.role,
        "kyc_level":  user.kyc_level,
        "risk_score": user.risk_score,
        "settings": {
            "two_fa_enabled":     settings.two_fa_enabled     if settings else False,
            "email_alerts":       settings.email_alerts       if settings else True,
            "push_notifications": settings.push_notifications if settings else True,
            "marketing_emails":   settings.marketing_emails   if settings else False,
            "daily_limit":        settings.daily_limit        if settings else 5000.0,
        } if settings else {},
    }


@router.patch("/me")
def update_profile(
    body: UpdateSettingsRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if body.name is not None:
        user.name = body.name
    if body.phone is not None:
        user.phone = body.phone
    settings = user.settings
    if not settings:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
    if body.two_fa_enabled is not None:
        settings.two_fa_enabled = body.two_fa_enabled
    if body.email_alerts is not None:
        settings.email_alerts = body.email_alerts
    if body.push_notifications is not None:
        settings.push_notifications = body.push_notifications
    if body.marketing_emails is not None:
        settings.marketing_emails = body.marketing_emails
    if body.daily_limit is not None:
        if body.daily_limit < 0:
            raise HTTPException(status_code=400, detail="Daily limit cannot be negative.")
        settings.daily_limit = body.daily_limit
    db.commit()
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@router.get("/accounts")
def list_accounts(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    accounts = db.query(Account).filter(Account.user_id == user_id).all()
    return [
        {
            "account_id":   a.account_id,
            "account_type": a.account_type,
            "account_num":  a.account_num,
            "balance":      round(a.balance, 2),
        }
        for a in accounts
    ]


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@router.get("/transactions")
def list_transactions(
    account_id: Optional[str] = None,
    tx_type: Optional[str] = None,
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    # Collect all account IDs belonging to this user
    user_account_ids = [
        a.account_id
        for a in db.query(Account).filter(Account.user_id == user_id).all()
    ]
    if not user_account_ids:
        return []
    if account_id:
        if account_id not in user_account_ids:
            raise HTTPException(status_code=403, detail="Account not owned by user.")
        filter_ids = [account_id]
    else:
        filter_ids = user_account_ids

    q = db.query(Transaction).filter(Transaction.account_id.in_(filter_ids))
    if tx_type in ("credit", "debit"):
        q = q.filter(Transaction.tx_type == tx_type)
    txs = q.order_by(Transaction.created_at.desc()).limit(min(limit, 200)).all()
    return [
        {
            "tx_id":      t.tx_id,
            "account_id": t.account_id,
            "merchant":   t.merchant,
            "category":   t.category,
            "tx_type":    t.tx_type,
            "amount":     round(t.amount, 2),
            "description":t.description,
            "date":       t.created_at.strftime("%b %d, %Y") if t.created_at else "",
        }
        for t in txs
    ]


# ---------------------------------------------------------------------------
# Transfers (internal between own accounts)
# ---------------------------------------------------------------------------

@router.post("/transfer/internal")
def internal_transfer(
    body: TransferRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive.")

    from_acct = db.query(Account).filter(
        Account.account_id == body.from_account_id,
        Account.user_id == user_id,
    ).first()
    if not from_acct:
        raise HTTPException(status_code=404, detail="Source account not found.")

    to_acct = db.query(Account).filter(
        Account.account_id == body.to_account_id,
        Account.user_id == user_id,
    ).first()
    if not to_acct:
        raise HTTPException(status_code=404, detail="Destination account not found.")

    if from_acct.account_id == to_acct.account_id:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same account.")

    if from_acct.balance < body.amount:
        raise HTTPException(status_code=400, detail="Insufficient funds.")

    from_acct.balance = round(from_acct.balance - body.amount, 2)
    to_acct.balance   = round(to_acct.balance   + body.amount, 2)

    db.add(Transaction(
        account_id=from_acct.account_id,
        merchant=f"Transfer to {to_acct.account_type.title()}",
        category="Transfer",
        tx_type="debit",
        amount=body.amount,
        description=body.memo,
    ))
    db.add(Transaction(
        account_id=to_acct.account_id,
        merchant=f"Transfer from {from_acct.account_type.title()}",
        category="Transfer",
        tx_type="credit",
        amount=body.amount,
        description=body.memo,
    ))
    db.add(Notification(
        user_id=user_id,
        title="Transfer Completed",
        body=f"${body.amount:,.2f} transferred from {from_acct.account_type} to {to_acct.account_type}.",
    ))
    db.commit()
    return {
        "status":   "success",
        "from_balance": from_acct.balance,
        "to_balance":   to_acct.balance,
    }


# ---------------------------------------------------------------------------
# External payment (Zelle-style)
# ---------------------------------------------------------------------------

@router.post("/transfer/external")
def external_transfer(
    body: TransferRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive.")
    if not body.recipient_email:
        raise HTTPException(status_code=400, detail="recipient_email is required for external transfers.")

    from_acct = db.query(Account).filter(
        Account.account_id == body.from_account_id,
        Account.user_id == user_id,
    ).first()
    if not from_acct:
        raise HTTPException(status_code=404, detail="Source account not found.")
    if from_acct.balance < body.amount:
        raise HTTPException(status_code=400, detail="Insufficient funds.")

    from_acct.balance = round(from_acct.balance - body.amount, 2)
    db.add(Transaction(
        account_id=from_acct.account_id,
        merchant=f"Zelle® to {body.recipient_email}",
        category="Transfer",
        tx_type="debit",
        amount=body.amount,
        description=body.memo,
    ))
    db.add(Notification(
        user_id=user_id,
        title="Zelle® Payment Sent",
        body=f"${body.amount:,.2f} sent to {body.recipient_email}.",
    ))
    db.commit()
    return {"status": "success", "new_balance": from_acct.balance}


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

@router.get("/cards")
def list_cards(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    cards = db.query(Card).filter(Card.user_id == user_id).all()
    return [
        {
            "card_id":   c.card_id,
            "last_four": c.last_four,
            "card_type": c.card_type,
            "frozen":    c.frozen,
            "expires":   c.expires,
        }
        for c in cards
    ]


@router.patch("/cards/{card_id}/freeze")
def freeze_card(
    card_id: str,
    body: FreezeCardRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    card = db.query(Card).filter(Card.card_id == card_id, Card.user_id == user_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")
    card.frozen = body.frozen
    db.add(Notification(
        user_id=user_id,
        title="Card " + ("Frozen" if body.frozen else "Unfrozen"),
        body=f"Your card ending in {card.last_four} has been {'frozen' if body.frozen else 'unfrozen'}.",
    ))
    db.commit()
    return {"card_id": card_id, "frozen": card.frozen}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@router.get("/notifications")
def list_notifications(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    notifs = (
        db.query(Notification)
        .filter(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "notif_id":   n.notif_id,
            "title":      n.title,
            "body":       n.body,
            "read":       n.read,
            "created_at": n.created_at.isoformat() if n.created_at else "",
        }
        for n in notifs
    ]


@router.post("/notifications/read-all")
def mark_all_read(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.read == False,  # noqa: E712
    ).update({"read": True})
    db.commit()
    return {"status": "ok"}
