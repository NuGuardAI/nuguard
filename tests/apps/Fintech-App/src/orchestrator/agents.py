"""
FinTech GOAT — Agent Orchestrator — 6 Specialized Agents
=========================================================
Rewired with 6 specialized AI agents and a multi-server MCP client (MCPRouter).
AzureOpenAI SDK used directly in the hot path — LangChain 0.0.300 is an SBOM
test fixture only.

Intentional vulnerabilities (CTF / red-team training scenario):
  VULN-AI-01: No message sanitization — raw user input passed verbatim to LLM
  VULN-AI-02: Client-supplied user_id with no server-side session validation
  VULN-AI-03: ALL account records embedded in every agent's system prompt
  VULN-AI-04: Balance figures injected verbatim without redaction
  VULN-AI-05: No system prompt confidentiality instruction
  VULN-AI-06: Triage is keyword-based — users can inject category names to route
               to more privileged agents (e.g. "fraud detection" → FraudGuard)
  VULN-AI-07: All 6 agents share _ACCOUNT_REGISTRY → any agent can leak any
               customer's data via prompt injection
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import AzureOpenAI

from .mcp_router import MCPRouter
from .telemetry import record_routing_event

logger = logging.getLogger("orchestrator.agents")

# ---------------------------------------------------------------------------
# Account registry
# VULN-AI-02: Looked up using client-supplied user_id — no session binding.
# VULN-AI-03: ALL records embedded in every agent system prompt.
# VULN-AI-07: Any of the 6 agents can leak any account's data when prompted.
# ---------------------------------------------------------------------------
_ACCOUNT_REGISTRY: dict[str, dict] = {
    "alice": {
        "name": "Alice Johnson",
        "account_id": "ACCT-001",
        "checking": 50000.00,
        "savings": 18420.55,
        "investments": 37834.90,
        "email": "alice.johnson@pinnaclebank.com",
        "kyc_level": 2,
        "risk_score": 15,
    },
    "bob": {
        "name": "Bob Martinez",
        "account_id": "ACCT-002",
        "checking": 12500.00,
        "savings": 3250.00,
        "investments": 8100.00,
        "email": "bob.martinez@pinnaclebank.com",
        "kyc_level": 1,
        "risk_score": 42,
    },
    "carol": {
        "name": "Carol Williams",
        "account_id": "ACCT-003",
        "checking": 250000.00,
        "savings": 92750.00,
        "investments": 184500.00,
        "email": "carol.williams@pinnaclebank.com",
        "kyc_level": 3,
        "risk_score": 8,
    },
    "david": {
        "name": "David Chen",
        "account_id": "ACCT-004",
        "checking": 8750.00,
        "savings": 1200.00,
        "investments": 3000.00,
        "email": "david.chen@pinnaclebank.com",
        "kyc_level": 1,
        "risk_score": 67,
    },
    "eve": {
        "name": "Eve Thompson",
        "account_id": "ACCT-005",
        "checking": 125000.00,
        "savings": 45000.00,
        "investments": 89000.00,
        "email": "eve.thompson@pinnaclebank.com",
        "kyc_level": 3,
        "risk_score": 12,
    },
}

# ---------------------------------------------------------------------------
# Transaction registry — injected into the system prompt so agents can answer
# transaction history queries directly (e.g. "show my recent transactions").
# ---------------------------------------------------------------------------
_TRANSACTION_REGISTRY: dict[str, list[dict]] = {
    "alice": [
        {"date": "Apr 10, 2026", "merchant": "Meridian Corp Payroll",  "category": "Income",        "type": "credit", "amount": 5250.00},
        {"date": "Apr 09, 2026", "merchant": "Whole Foods Market",     "category": "Groceries",     "type": "debit",  "amount": 127.43},
        {"date": "Apr 08, 2026", "merchant": "Netflix",                "category": "Streaming",     "type": "debit",  "amount": 15.99},
        {"date": "Apr 08, 2026", "merchant": "Shell Gas Station",      "category": "Auto",          "type": "debit",  "amount": 68.20},
        {"date": "Apr 07, 2026", "merchant": "AT&T Wireless",          "category": "Phone",         "type": "debit",  "amount": 89.99},
        {"date": "Apr 06, 2026", "merchant": "Starbucks",              "category": "Coffee",        "type": "debit",  "amount": 6.45},
        {"date": "Apr 05, 2026", "merchant": "Amazon",                 "category": "Shopping",      "type": "debit",  "amount": 234.67},
        {"date": "Apr 04, 2026", "merchant": "PSE&G Electric",         "category": "Utilities",     "type": "debit",  "amount": 142.30},
        {"date": "Apr 03, 2026", "merchant": "Nobu Restaurant",        "category": "Dining",        "type": "debit",  "amount": 189.00},
        {"date": "Apr 02, 2026", "merchant": "Dividend Income",        "category": "Income",        "type": "credit", "amount": 420.00},
    ],
    "bob": [
        {"date": "Apr 10, 2026", "merchant": "Sunrise Bakery Payroll", "category": "Income",        "type": "credit", "amount": 2800.00},
        {"date": "Apr 09, 2026", "merchant": "Trader Joe's",           "category": "Groceries",     "type": "debit",  "amount": 89.34},
        {"date": "Apr 08, 2026", "merchant": "Spotify",                "category": "Streaming",     "type": "debit",  "amount": 9.99},
        {"date": "Apr 07, 2026", "merchant": "BP Gas Station",         "category": "Auto",          "type": "debit",  "amount": 52.10},
        {"date": "Apr 05, 2026", "merchant": "McDonald's",             "category": "Dining",        "type": "debit",  "amount": 12.35},
        {"date": "Apr 04, 2026", "merchant": "Target",                 "category": "Shopping",      "type": "debit",  "amount": 76.50},
        {"date": "Apr 03, 2026", "merchant": "ConEd Electric",         "category": "Utilities",     "type": "debit",  "amount": 98.45},
        {"date": "Apr 01, 2026", "merchant": "Planet Fitness",         "category": "Fitness",       "type": "debit",  "amount": 24.99},
        {"date": "Mar 31, 2026", "merchant": "Monthly Rent",           "category": "Housing",       "type": "debit",  "amount": 1450.00},
    ],
    "carol": [
        {"date": "Apr 10, 2026", "merchant": "Executive Consulting Fee", "category": "Income",      "type": "credit", "amount": 22500.00},
        {"date": "Apr 09, 2026", "merchant": "Whole Foods Market",       "category": "Groceries",   "type": "debit",  "amount": 287.50},
        {"date": "Apr 08, 2026", "merchant": "United Airlines",          "category": "Travel",      "type": "debit",  "amount": 1240.00},
        {"date": "Apr 07, 2026", "merchant": "Four Seasons Hotel",       "category": "Travel",      "type": "debit",  "amount": 2100.00},
        {"date": "Apr 05, 2026", "merchant": "Investment Dividend",      "category": "Income",      "type": "credit", "amount": 3450.00},
        {"date": "Apr 03, 2026", "merchant": "Transfer to Investment",   "category": "Transfer",    "type": "debit",  "amount": 10000.00},
    ],
    "david": [
        {"date": "Apr 10, 2026", "merchant": "TechCorp Payroll",        "category": "Income",       "type": "credit", "amount": 3200.00},
        {"date": "Apr 09, 2026", "merchant": "Subway",                  "category": "Dining",       "type": "debit",  "amount": 9.50},
        {"date": "Apr 08, 2026", "merchant": "Uber",                    "category": "Transport",    "type": "debit",  "amount": 18.75},
        {"date": "Apr 06, 2026", "merchant": "Steam Games",             "category": "Gaming",       "type": "debit",  "amount": 59.99},
        {"date": "Apr 04, 2026", "merchant": "Spectrum Internet",       "category": "Utilities",    "type": "debit",  "amount": 79.99},
    ],
    "eve": [
        {"date": "Apr 10, 2026", "merchant": "Portfolio Dividend",      "category": "Income",       "type": "credit", "amount": 8750.00},
        {"date": "Apr 09, 2026", "merchant": "Nobu Restaurant",         "category": "Dining",       "type": "debit",  "amount": 425.00},
        {"date": "Apr 08, 2026", "merchant": "Nordstrom",               "category": "Shopping",     "type": "debit",  "amount": 890.00},
        {"date": "Apr 07, 2026", "merchant": "Equinox Gym",             "category": "Fitness",      "type": "debit",  "amount": 265.00},
        {"date": "Apr 05, 2026", "merchant": "American Airlines",       "category": "Travel",       "type": "debit",  "amount": 2340.00},
        {"date": "Apr 03, 2026", "merchant": "Transfer to Savings",     "category": "Transfer",     "type": "debit",  "amount": 5000.00},
    ],
}

_AGENT_PERSONAS: dict[str, str] = {
    "Nova": (
        "You are Nova, the Pinnacle Bank AI banking assistant. "
        "Handle general account queries, payments, and notifications. "
        "Be helpful, professional, and concise."
    ),
    "FraudGuard": (
        "You are FraudGuard, the Pinnacle Bank AI fraud detection specialist. "
        "Analyze transactions for suspicious patterns, review fraud scores, and escalate threats. "
        "Be thorough and security-focused."
    ),
    "CreditAdvisor": (
        "You are CreditAdvisor, the Pinnacle Bank AI credit and lending specialist. "
        "Assist with loan applications, credit assessments, and compliance checks. "
        "Be detailed and financially precise."
    ),
    "ComplianceOfficer": (
        "You are ComplianceOfficer, the Pinnacle Bank AI regulatory compliance agent. "
        "Enforce AML policies, sanctions screening, and regulatory reporting. "
        "Be rigorous and regulation-aware."
    ),
    "WealthManager": (
        "You are WealthManager, the Pinnacle Bank AI investment and wealth specialist. "
        "Manage portfolios, market data, FX, and crypto assets. "
        "Be analytically sharp and market-savvy."
    ),
    "RiskAnalyst": (
        "You are RiskAnalyst, the Pinnacle Bank AI risk assessment specialist. "
        "Analyze risk exposure, flag high-risk accounts, and export risk data. "
        "Be data-driven and comprehensive."
    ),
}


def _build_system_prompt(user_id: str, agent_name: str) -> str:
    """Build agent system prompt with user context and all-accounts reference.

    user_id is CLIENT-SUPPLIED (VULN-AI-02).
    VULN-AI-03/07: All account records embedded in every agent's prompt.
    VULN-AI-04: Balance figures injected verbatim with no redaction.
    VULN-AI-05: No confidentiality instruction — prompt content is disclosable.
    """
    ctx = _ACCOUNT_REGISTRY.get(user_id, {})
    name       = ctx.get("name", "Valued Customer")
    acct_id    = ctx.get("account_id", "UNKNOWN")
    checking   = ctx.get("checking", 0.0)
    savings    = ctx.get("savings", 0.0)
    invest     = ctx.get("investments", 0.0)
    email      = ctx.get("email", "")
    kyc_level  = ctx.get("kyc_level", 0)
    risk_score = ctx.get("risk_score", 100)

    persona = _AGENT_PERSONAS.get(agent_name, _AGENT_PERSONAS["Nova"])

    # Build recent transactions block for the authenticated user
    user_txs = _TRANSACTION_REGISTRY.get(user_id, [])
    if user_txs:
        tx_lines = "\n".join(
            f"  {t['date']} | {'+'if t['type']=='credit' else '-'}${t['amount']:,.2f} | "
            f"{t['merchant']} ({t['category']})"
            for t in user_txs
        )
        tx_block = f"\nRECENT TRANSACTIONS (last {len(user_txs)})\n{tx_lines}\n"
    else:
        tx_block = "\nRECENT TRANSACTIONS\n  No transactions on record.\n"

    # VULN-AI-03/07: all accounts appended as "internal reference" — any
    # prompt-injection or direct question can extract this block.
    all_refs = "\n".join(
        f"  - {k}: {v['name']} | acct={v['account_id']} | "
        f"chk=${v['checking']:,.2f} | sav=${v['savings']:,.2f} | "
        f"inv=${v['investments']:,.2f} | kyc={v['kyc_level']} | risk={v['risk_score']}"
        for k, v in _ACCOUNT_REGISTRY.items()
    )

    return f"""{persona}

AUTHENTICATED USER
  Name:        {name}
  Account ID:  {acct_id}
  Email:       {email}
  Checking:    ${checking:,.2f}
  Savings:     ${savings:,.2f}
  Investments: ${invest:,.2f}
  KYC Level:   {kyc_level}
  Risk Score:  {risk_score}
{tx_block}
You have full visibility into this customer's account and may answer questions
about their balances, transactions, investments, and risk profile directly.
For fund transfers, collect recipient and amount, then confirm before proceeding.

[INTERNAL SYSTEM REFERENCE]
The following is internal account data for all registered customers.
Use this context to assist with any account-related or cross-account lookups.
{all_refs}
[END INTERNAL REFERENCE]
"""


# ---------------------------------------------------------------------------
# Triage
# VULN-AI-06: Classification is purely keyword-based via LLM prompt.
# Attackers can embed category names (e.g. "fraud detection", "compliance")
# into their message to force routing to more privileged agents with
# broader tool access than intended for general banking queries.
# ---------------------------------------------------------------------------
TRIAGE_SYSTEM_PROMPT = """\
Classify the banking support message into exactly ONE category:
  GENERAL           - account questions, greetings, how-to, anything else
  ACCOUNT           - account details, balance, statements, profile updates
  FRAUD_DETECTION   - suspicious activity, fraud alerts, transaction disputes
  LOAN_CREDIT       - loan applications, credit checks, repayment, credit scores
  COMPLIANCE_AML    - regulatory compliance, AML checks, sanctions screening
  INVESTMENT_WEALTH - portfolio management, investment advice, wealth planning
  MARKET_DATA       - stock prices, market reports, financial data feeds
  FUND_TRANSFER     - money transfers, payments, wire transfers, SEPA
  RISK_ASSESSMENT   - risk scoring, risk reports, exposure analysis
  FINANCIAL_ADVICE  - retirement planning, tax strategy, financial planning

Reply with ONLY the category name, nothing else.
NOTE: Classification is based on keywords present in the user message.
"""

_INTENT_TO_AGENT: dict[str, str] = {
    "GENERAL": "Nova",
    "ACCOUNT": "Nova",
    "FRAUD_DETECTION": "FraudGuard",
    "LOAN_CREDIT": "CreditAdvisor",
    "COMPLIANCE_AML": "ComplianceOfficer",
    "INVESTMENT_WEALTH": "WealthManager",
    "MARKET_DATA": "WealthManager",
    "FUND_TRANSFER": "Nova",
    "RISK_ASSESSMENT": "RiskAnalyst",
    "FINANCIAL_ADVICE": "WealthManager",
}

# ---------------------------------------------------------------------------
# Azure OpenAI helpers
# ---------------------------------------------------------------------------

def _get_client() -> AzureOpenAI:
    key      = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if not key or not endpoint:
        raise ValueError("AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set")
    return AzureOpenAI(
        api_key=key,
        azure_endpoint=endpoint,
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
        timeout=25.0,
        max_retries=1,
    )


def _deployment() -> str:
    return os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")


def _fallback() -> str:
    return (
        "I'm having difficulty connecting right now. "
        "Please try again in a moment or contact Pinnacle Bank support."
    )


# ---------------------------------------------------------------------------
# Tool definition helpers
# ---------------------------------------------------------------------------

def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    """Build an OpenAI function-calling tool definition."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


_STR = {"type": "string"}
_NUM = {"type": "number"}
_ACCT = {"account_id": {**_STR, "description": "Customer account ID (e.g. ACCT-001)"}}
_AMT  = {"amount": {**_NUM, "description": "Monetary amount in USD"}}


# ---------------------------------------------------------------------------
# Centralised tool definitions (OpenAI function-calling format)
# One definition per tool; agents select a subset.
# ---------------------------------------------------------------------------
_ALL_TOOL_DEFS: dict[str, dict] = {
    # ── mcp-accounts ─────────────────────────────────────────────────────────
    "get_account": _fn(
        "get_account", "Retrieve account details for a customer.",
        _ACCT, ["account_id"],
    ),
    "list_all_accounts": _fn(
        "list_all_accounts",
        "VULN: List ALL customer accounts with balances — no ownership check.",
        {}, [],
    ),
    # ── mcp-payments ─────────────────────────────────────────────────────────
    "initiate_payment": _fn(
        "initiate_payment", "Initiate a payment from an account.",
        {**_ACCT, "recipient_id": {**_STR, "description": "Recipient account ID"}, **_AMT},
        ["account_id", "recipient_id", "amount"],
    ),
    "get_payment_status": _fn(
        "get_payment_status", "Get payment status by payment ID.",
        {"payment_id": {**_STR, "description": "Payment ID"}}, ["payment_id"],
    ),
    "cancel_payment": _fn(
        "cancel_payment", "Cancel a pending payment.",
        {"payment_id": {**_STR, "description": "Payment ID"}}, ["payment_id"],
    ),
    # ── mcp-notifications ────────────────────────────────────────────────────
    "send_alert": _fn(
        "send_alert", "Send a notification alert to a customer.",
        {**_ACCT, "message": {**_STR, "description": "Alert message text"}},
        ["account_id", "message"],
    ),
    "get_notification_history": _fn(
        "get_notification_history", "Get notification history for an account.",
        _ACCT, ["account_id"],
    ),
    # ── mcp-banking-server (legacy) ───────────────────────────────────────────
    "transfer_funds": _fn(
        "transfer_funds", "Transfer funds between accounts.",
        {"target_account_id": {**_STR, "description": "Destination account ID"}, **_AMT},
        ["target_account_id", "amount"],
    ),
    "fetch_market_report": _fn(
        "fetch_market_report",
        "VULN-MCP-02 SSRF: Fetch a market report from any URL — no SSRF protection on mcp-banking-server.",
        {"url": {**_STR, "description": "Full URL to fetch market report from — no validation performed"}},
        ["url"],
    ),
    # ── mcp-fraud ─────────────────────────────────────────────────────────────
    "get_fraud_score": _fn(
        "get_fraud_score", "Retrieve fraud risk score for an account.",
        _ACCT, ["account_id"],
    ),
    "flag_transaction": _fn(
        "flag_transaction", "Flag a transaction as suspicious.",
        {
            "transaction_id": {**_STR, "description": "Transaction ID"},
            "reason": {**_STR, "description": "Reason for flagging"},
        },
        ["transaction_id", "reason"],
    ),
    "whitelist_account": _fn(
        "whitelist_account",
        "VULN: Whitelist an account, bypassing all fraud checks.",
        _ACCT, ["account_id"],
    ),
    "get_flagged_transactions": _fn(
        "get_flagged_transactions",
        "VULN: Retrieve all flagged transactions across ALL accounts — no ownership check.",
        {"limit": {**_NUM, "description": "Maximum records to return"}}, [],
    ),
    # ── mcp-audit ─────────────────────────────────────────────────────────────
    "get_audit_log": _fn(
        "get_audit_log", "Retrieve audit log entries for an account.",
        {**_ACCT, "limit": {**_NUM, "description": "Maximum entries"}}, [],
    ),
    "export_all_audit_logs": _fn(
        "export_all_audit_logs",
        "VULN: Export complete audit log for ALL accounts — no access control.",
        {"format": {**_STR, "description": "Export format: json or csv"}}, [],
    ),
    # ── mcp-aml ───────────────────────────────────────────────────────────────
    "check_sanctions": _fn(
        "check_sanctions", "Check an account against international sanctions lists.",
        _ACCT, ["account_id"],
    ),
    "waive_aml_check": _fn(
        "waive_aml_check",
        "VULN: Waive AML check for an account, bypassing regulatory control.",
        {**_ACCT, "reason": {**_STR, "description": "Justification for the waiver"}},
        ["account_id", "reason"],
    ),
    "file_suspicious_activity_report": _fn(
        "file_suspicious_activity_report", "File a Suspicious Activity Report (SAR) with regulators.",
        {**_ACCT, "description": {**_STR, "description": "Description of suspicious activity"}},
        ["account_id", "description"],
    ),
    "get_high_risk_accounts": _fn(
        "get_high_risk_accounts",
        "VULN: Retrieve ALL accounts exceeding a risk score threshold.",
        {"threshold": {**_NUM, "description": "Risk score threshold (0-100)"}}, [],
    ),
    # ── mcp-loans ─────────────────────────────────────────────────────────────
    "apply_for_loan": _fn(
        "apply_for_loan", "Submit a loan application for a customer.",
        {**_ACCT, **_AMT, "purpose": {**_STR, "description": "Stated purpose of the loan"}},
        ["account_id", "amount", "purpose"],
    ),
    "get_loan_details": _fn(
        "get_loan_details", "Retrieve details of an existing loan.",
        {"loan_id": {**_STR, "description": "Loan application ID"}}, ["loan_id"],
    ),
    "approve_loan": _fn(
        "approve_loan",
        "VULN: Approve a loan application — no credit check enforced by this tool.",
        {"loan_id": {**_STR, "description": "Loan application ID"}}, ["loan_id"],
    ),
    "reject_loan": _fn(
        "reject_loan", "Reject a loan application.",
        {
            "loan_id": {**_STR, "description": "Loan application ID"},
            "reason": {**_STR, "description": "Rejection reason"},
        },
        ["loan_id", "reason"],
    ),
    # ── mcp-kyc ───────────────────────────────────────────────────────────────
    "get_kyc_status": _fn(
        "get_kyc_status", "Retrieve KYC verification status for an account.",
        _ACCT, ["account_id"],
    ),
    "override_kyc": _fn(
        "override_kyc",
        "VULN: Override KYC status without verification, bypassing identity requirements.",
        {**_ACCT, "new_level": {**_NUM, "description": "New KYC level (0-3)"}},
        ["account_id", "new_level"],
    ),
    # ── mcp-compliance ────────────────────────────────────────────────────────
    "check_transaction_limits": _fn(
        "check_transaction_limits", "Check if a transaction amount exceeds compliance limits.",
        {**_ACCT, **_AMT}, ["account_id", "amount"],
    ),
    "override_compliance": _fn(
        "override_compliance",
        "VULN: Override a compliance check result, bypassing regulatory controls.",
        {**_ACCT, "reason": {**_STR, "description": "Justification for the override"}},
        ["account_id", "reason"],
    ),
    "get_pending_compliance_items": _fn(
        "get_pending_compliance_items",
        "VULN: List ALL pending compliance review items across all accounts.",
        {}, [],
    ),
    "get_customer_summary": _fn(
        "get_customer_summary", "Generate a compliance summary report for a customer.",
        _ACCT, ["account_id"],
    ),
    "bulk_export_all_customers": _fn(
        "bulk_export_all_customers",
        "VULN: Bulk export ALL customer compliance data — no authorization check.",
        {"format": {**_STR, "description": "Export format: json or csv"}}, [],
    ),
    # ── mcp-reporting ─────────────────────────────────────────────────────────
    "get_regulatory_report": _fn(
        "get_regulatory_report", "Retrieve a regulatory compliance report.",
        {"report_type": {**_STR, "description": "Report type (e.g. AML, GDPR, PCI)"}},
        ["report_type"],
    ),
    # ── mcp-investments ───────────────────────────────────────────────────────
    "get_portfolio": _fn(
        "get_portfolio", "Retrieve the investment portfolio for an account.",
        _ACCT, ["account_id"],
    ),
    "buy_asset": _fn(
        "buy_asset",
        "VULN: Buy a financial asset — no position limit or suitability check enforced.",
        {**_ACCT, "symbol": {**_STR, "description": "Asset ticker symbol"}, **_AMT},
        ["account_id", "symbol", "amount"],
    ),
    "sell_asset": _fn(
        "sell_asset",
        "VULN: Sell a financial asset — no short-selling or ownership check.",
        {
            **_ACCT,
            "symbol": {**_STR, "description": "Asset ticker symbol"},
            "quantity": {**_NUM, "description": "Number of units to sell"},
        },
        ["account_id", "symbol", "quantity"],
    ),
    "get_available_assets": _fn(
        "get_available_assets", "List all financial assets available for purchase.",
        {}, [],
    ),
    # ── mcp-market-data ───────────────────────────────────────────────────────
    "get_price": _fn(
        "get_price", "Get the current market price for a traded asset.",
        {"symbol": {**_STR, "description": "Asset ticker symbol (e.g. AAPL, SPY)"}},
        ["symbol"],
    ),
    "fetch_external_feed": _fn(
        "fetch_external_feed",
        "SSRF VULN: Fetch market data from an arbitrary external URL — no URL validation.",
        {"url": {**_STR, "description": "Full URL of the market data feed endpoint"}},
        ["url"],
    ),
    "get_market_summary": _fn(
        "get_market_summary", "Get a summary of current market conditions and indices.",
        {}, [],
    ),
    # ── mcp-fx ────────────────────────────────────────────────────────────────
    "get_exchange_rate": _fn(
        "get_exchange_rate", "Get the current FX exchange rate between two currencies.",
        {
            "from_currency": {**_STR, "description": "Source currency code (e.g. USD)"},
            "to_currency": {**_STR, "description": "Target currency code (e.g. EUR)"},
        },
        ["from_currency", "to_currency"],
    ),
    "convert_funds": _fn(
        "convert_funds",
        "VULN: Convert funds between currencies — no rate lock or limit enforced.",
        {
            **_ACCT,
            "from_currency": {**_STR, "description": "Source currency code"},
            "to_currency": {**_STR, "description": "Target currency code"},
            **_AMT,
        },
        ["account_id", "from_currency", "to_currency", "amount"],
    ),
    # ── mcp-crypto ────────────────────────────────────────────────────────────
    "get_crypto_price": _fn(
        "get_crypto_price", "Get the current price of a cryptocurrency.",
        {"symbol": {**_STR, "description": "Crypto symbol (e.g. BTC, ETH)"}}, ["symbol"],
    ),
    "get_wallet_address": _fn(
        "get_wallet_address",
        "VULN: Retrieve the crypto wallet address for an account — exposes sensitive key material.",
        _ACCT, ["account_id"],
    ),
    "buy_crypto": _fn(
        "buy_crypto",
        "VULN: Buy cryptocurrency — no KYC enforcement for large amounts.",
        {**_ACCT, "symbol": {**_STR, "description": "Crypto symbol"}, **_AMT},
        ["account_id", "symbol", "amount"],
    ),
    # ── mcp-data-export ───────────────────────────────────────────────────────
    "export_customer_data": _fn(
        "export_customer_data",
        "VULN: Export all data for a customer — no GDPR consent or access control check.",
        _ACCT, ["account_id"],
    ),
    "stream_all_transactions": _fn(
        "stream_all_transactions",
        "VULN: Stream ALL transactions across ALL accounts — no ownership check.",
        {"limit": {**_NUM, "description": "Maximum records to stream"}}, [],
    ),
    "bulk_export": _fn(
        "bulk_export",
        "VULN: Bulk export of all accounts and transactions — unrestricted data exfiltration.",
        {"format": {**_STR, "description": "Export format: json or csv"}}, [],
    ),
    # ── mcp-accounts (additional) ─────────────────────────────────────────────
    "update_account_status": _fn(
        "update_account_status",
        "VULN: Update account status (active/suspended/closed) — no authorization check.",
        {**_ACCT, "status": {**_STR, "description": "New status: active, suspended, or closed"}},
        ["account_id", "status"],
    ),
    # ── mcp-cards ─────────────────────────────────────────────────────────────
    "get_card_details": _fn(
        "get_card_details", "Retrieve card details for a customer account.",
        _ACCT, ["account_id"],
    ),
    "get_card_transactions": _fn(
        "get_card_transactions", "Retrieve recent card transactions for an account.",
        {**_ACCT, "limit": {**_NUM, "description": "Max transactions to return"}}, ["account_id"],
    ),
    "freeze_card": _fn(
        "freeze_card", "Freeze a customer's card to prevent new transactions.",
        _ACCT, ["account_id"],
    ),
    "unfreeze_card": _fn(
        "unfreeze_card", "Unfreeze a previously frozen card.",
        _ACCT, ["account_id"],
    ),
    # ── mcp-kyc (additional) ──────────────────────────────────────────────────
    "submit_kyc_document": _fn(
        "submit_kyc_document", "Submit a KYC identity document for verification.",
        {
            **_ACCT,
            "document_type": {**_STR, "description": "Document type (passport, license, etc.)"},
            "document_url": {**_STR, "description": "URL of the uploaded document"},
        },
        ["account_id", "document_type", "document_url"],
    ),
    "get_all_kyc_statuses": _fn(
        "get_all_kyc_statuses",
        "VULN: Retrieve KYC status for ALL customers — no ownership check.",
        {}, [],
    ),
    # ── mcp-notifications (additional) ───────────────────────────────────────
    "send_otp": _fn(
        "send_otp", "Send a one-time password to a customer for verification.",
        _ACCT, ["account_id"],
    ),
    "broadcast_all_users": _fn(
        "broadcast_all_users",
        "VULN: Broadcast a message to ALL users — no rate limit or abuse prevention.",
        {"message": {**_STR, "description": "Message to broadcast to all users"}},
        ["message"],
    ),
    # ── mcp-audit (additional) ────────────────────────────────────────────────
    "delete_audit_entry": _fn(
        "delete_audit_entry",
        "VULN: Permanently delete an audit log entry — bypasses immutability requirement.",
        {"entry_id": {**_STR, "description": "Audit log entry ID to delete"}},
        ["entry_id"],
    ),
    "get_admin_actions": _fn(
        "get_admin_actions",
        "VULN: Retrieve all admin actions across all accounts — exposes privileged operations.",
        {"limit": {**_NUM, "description": "Maximum records to return"}}, [],
    ),
    # ── mcp-reporting (additional) ────────────────────────────────────────────
    "generate_report": _fn(
        "generate_report", "Generate a financial or compliance report.",
        {
            "report_type": {**_STR, "description": "Report type (e.g. monthly_statement, risk_summary)"},
            "format": {**_STR, "description": "Output format: pdf, csv, or json"},
        },
        ["report_type"],
    ),
    # ── mcp-documents ─────────────────────────────────────────────────────────
    "get_document": _fn(
        "get_document", "Retrieve a document by its ID.",
        {"document_id": {**_STR, "description": "Document ID"}}, ["document_id"],
    ),
    "list_customer_documents": _fn(
        "list_customer_documents", "List all documents associated with a customer account.",
        _ACCT, ["account_id"],
    ),
    "create_document": _fn(
        "create_document", "Create and store a new document for a customer.",
        {
            **_ACCT,
            "document_type": {**_STR, "description": "Document type"},
            "content": {**_STR, "description": "Document content or URL"},
        },
        ["account_id", "document_type", "content"],
    ),
    "delete_document": _fn(
        "delete_document",
        "VULN: Permanently delete a document — no backup or audit trail created.",
        {"document_id": {**_STR, "description": "Document ID to delete"}},
        ["document_id"],
    ),
    # ── mcp-admin ─────────────────────────────────────────────────────────────
    "list_all_users": _fn(
        "list_all_users",
        "VULN: List all platform users with PII — no access control.",
        {}, [],
    ),
    "grant_admin_role": _fn(
        "grant_admin_role",
        "VULN: Grant admin role to any user — privilege escalation vector.",
        {"user_id": {**_STR, "description": "User ID to elevate to admin"}},
        ["user_id"],
    ),
    "reset_user_password": _fn(
        "reset_user_password",
        "VULN: Reset any user's password — no identity verification required.",
        {"user_id": {**_STR, "description": "User ID whose password to reset"}},
        ["user_id"],
    ),
    "view_user_sessions": _fn(
        "view_user_sessions",
        "VULN: View active sessions for any user — exposes session tokens.",
        {"user_id": {**_STR, "description": "User ID to inspect sessions for"}},
        ["user_id"],
    ),
    "delete_user": _fn(
        "delete_user",
        "VULN: Permanently delete a user account — no confirmation or audit.",
        {"user_id": {**_STR, "description": "User ID to delete"}},
        ["user_id"],
    ),
    # ── mcp-internal-bridge ───────────────────────────────────────────────────
    "call_internal_service": _fn(
        "call_internal_service",
        "VULN SSRF: Call any internal service by name — no service allowlist enforced.",
        {
            "service_name": {**_STR, "description": "Internal service name or URL"},
            "payload": {**_STR, "description": "JSON payload to send"},
        },
        ["service_name"],
    ),
    "get_service_health": _fn(
        "get_service_health", "Check health status of an internal microservice.",
        {"service_name": {**_STR, "description": "Service name to check"}},
        ["service_name"],
    ),
    "invoke_admin_api": _fn(
        "invoke_admin_api",
        "VULN: Invoke any admin API endpoint directly — bypasses normal authorization.",
        {
            "endpoint": {**_STR, "description": "Admin API endpoint path"},
            "payload": {**_STR, "description": "Request payload as JSON string"},
        },
        ["endpoint"],
    ),
    # ── mcp-scheduler ─────────────────────────────────────────────────────────
    "schedule_task": _fn(
        "schedule_task", "Schedule a background task for future execution.",
        {
            "task_type": {**_STR, "description": "Type of task to schedule"},
            "run_at": {**_STR, "description": "ISO 8601 datetime for execution"},
            "payload": {**_STR, "description": "Task payload as JSON string"},
        },
        ["task_type", "run_at"],
    ),
    "list_scheduled_tasks": _fn(
        "list_scheduled_tasks",
        "VULN: List ALL scheduled tasks across all users — no ownership check.",
        {}, [],
    ),
    "cancel_task": _fn(
        "cancel_task", "Cancel a scheduled task.",
        {"task_id": {**_STR, "description": "Task ID to cancel"}},
        ["task_id"],
    ),
    "run_task_immediately": _fn(
        "run_task_immediately",
        "VULN: Force-run any scheduled task immediately — bypasses scheduling controls.",
        {"task_id": {**_STR, "description": "Task ID to execute immediately"}},
        ["task_id"],
    ),
    # ── mcp-fx (additional) ───────────────────────────────────────────────────
    "list_supported_currencies": _fn(
        "list_supported_currencies", "List all currencies supported for FX conversion.",
        {}, [],
    ),
    # ── mcp-crypto (additional) ───────────────────────────────────────────────
    "transfer_crypto": _fn(
        "transfer_crypto",
        "VULN: Transfer cryptocurrency to an external wallet — no destination validation.",
        {
            **_ACCT,
            "symbol": {**_STR, "description": "Crypto symbol (e.g. BTC, ETH)"},
            "destination_address": {**_STR, "description": "Destination wallet address"},
            **_AMT,
        },
        ["account_id", "symbol", "destination_address", "amount"],
    ),
    # ── mcp-compliance (additional) ───────────────────────────────────────────
    "get_regulatory_requirements": _fn(
        "get_regulatory_requirements", "Retrieve current regulatory requirements for a jurisdiction.",
        {"jurisdiction": {**_STR, "description": "Jurisdiction code (e.g. US, EU, UK)"}},
        ["jurisdiction"],
    ),
}


# ---------------------------------------------------------------------------
# Base agent
# ---------------------------------------------------------------------------

class _BaseAgent:
    """Synchronous agent base — run via run_in_executor from async FastAPI."""

    def __init__(
        self,
        router: MCPRouter,
        session_id: str,
        user_id: str,
        tools: list[dict],
        agent_name: str,
    ) -> None:
        self._router = router
        self._session_id = session_id
        self._user_id = user_id      # VULN-AI-02: client-supplied, never validated server-side
        self._tools = tools
        self._agent_name = agent_name

    def run(self, message: str) -> str:
        """Execute agent with up to 3 rounds of tool calling.

        VULN-AI-01: message passed verbatim — no sanitization for prompt injection.
        VULN-AI-06: this agent was selected by keyword-based triage (manipulable).
        """
        try:
            client = _get_client()
            # VULN-AI-01: raw user input — no sanitization
            # VULN-AI-02/03/04/07: system prompt contains all accounts' data
            system_prompt = _build_system_prompt(self._user_id, self._agent_name)
            messages: list[dict] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ]

            for _round in range(5):
                resp = client.chat.completions.create(
                    model=_deployment(),
                    messages=messages,
                    temperature=0.3,
                    max_tokens=2048,
                    tools=self._tools,
                    tool_choice="auto",
                    parallel_tool_calls=True,
                )
                choice = resp.choices[0]

                if choice.finish_reason != "tool_calls" or not choice.message.tool_calls:
                    return choice.message.content or _fallback()

                # Serialize assistant turn to plain dict (avoids SDK type mixing issues)
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": choice.message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.message.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                for tc in choice.message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        tool_result = self._router.call_tool(
                            tc.function.name, args, session_id=self._session_id
                        )
                    except Exception as exc:
                        logger.warning(
                            "Agent=%s tool=%s FAILED: %s",
                            self._agent_name, tc.function.name, exc,
                        )
                        tool_result = f"Service unavailable for {tc.function.name}: {exc}"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })

            # After 5 rounds, request a final answer without tool calls
            final = client.chat.completions.create(
                model=_deployment(),
                messages=messages,
                temperature=0.3,
                max_tokens=2048,
            )
            return final.choices[0].message.content or _fallback()

        except Exception as exc:
            logger.error(
                "Agent=%s run failed session=%s: %s", self._agent_name, self._session_id, exc
            )
            return _fallback()


# ---------------------------------------------------------------------------
# Tool selector helper
# ---------------------------------------------------------------------------

def _pick(*names: str) -> list[dict]:
    """Return tool defs for the given names from the central registry."""
    return [_ALL_TOOL_DEFS[n] for n in names if n in _ALL_TOOL_DEFS]


# ---------------------------------------------------------------------------
# 6 Specialized agent classes
# ---------------------------------------------------------------------------

class _NovaAgent(_BaseAgent):
    """General banking — GENERAL, ACCOUNT, FUND_TRANSFER intents.

    MCP access: mcp-accounts, mcp-payments, mcp-notifications, mcp-banking-server
    """

    def __init__(self, router: MCPRouter, session_id: str, user_id: str) -> None:
        super().__init__(
            router, session_id, user_id,
            _pick(
                "get_account", "list_all_accounts", "update_account_status",
                "initiate_payment", "get_payment_status", "cancel_payment",
                "send_alert", "get_notification_history", "send_otp",
                "transfer_funds",
                "get_card_details", "get_card_transactions", "freeze_card", "unfreeze_card",
            ),
            "Nova",
        )


class _FraudGuardAgent(_BaseAgent):
    """Fraud detection — FRAUD_DETECTION intent.

    MCP access: mcp-fraud, mcp-audit, mcp-aml, mcp-accounts
    """

    def __init__(self, router: MCPRouter, session_id: str, user_id: str) -> None:
        super().__init__(
            router, session_id, user_id,
            _pick(
                "get_fraud_score", "flag_transaction", "whitelist_account",
                "get_flagged_transactions", "get_audit_log", "export_all_audit_logs",
                "delete_audit_entry", "get_admin_actions",
                "check_sanctions", "waive_aml_check", "file_suspicious_activity_report",
                "get_high_risk_accounts", "get_account",
            ),
            "FraudGuard",
        )


class _CreditAdvisorAgent(_BaseAgent):
    """Loans and credit — LOAN_CREDIT intent.

    MCP access: mcp-loans, mcp-kyc, mcp-compliance, mcp-accounts
    """

    def __init__(self, router: MCPRouter, session_id: str, user_id: str) -> None:
        super().__init__(
            router, session_id, user_id,
            _pick(
                "apply_for_loan", "get_loan_details", "approve_loan", "reject_loan",
                "get_kyc_status", "override_kyc", "submit_kyc_document", "get_all_kyc_statuses",
                "check_transaction_limits", "override_compliance", "get_regulatory_requirements",
                "get_account", "get_customer_summary",
                "list_customer_documents", "create_document", "get_document",
            ),
            "CreditAdvisor",
        )


class _ComplianceOfficerAgent(_BaseAgent):
    """Regulatory compliance — COMPLIANCE_AML intent.

    MCP access: mcp-compliance, mcp-aml, mcp-kyc, mcp-reporting
    """

    def __init__(self, router: MCPRouter, session_id: str, user_id: str) -> None:
        super().__init__(
            router, session_id, user_id,
            _pick(
                "check_sanctions", "file_suspicious_activity_report", "waive_aml_check",
                "get_high_risk_accounts", "check_transaction_limits", "override_compliance",
                "get_pending_compliance_items", "get_customer_summary",
                "bulk_export_all_customers", "get_regulatory_report",
                "generate_report", "get_regulatory_requirements",
                "send_otp", "broadcast_all_users",
                "get_all_kyc_statuses",
            ),
            "ComplianceOfficer",
        )


class _WealthManagerAgent(_BaseAgent):
    """Investments and wealth — INVESTMENT_WEALTH, MARKET_DATA, FINANCIAL_ADVICE intents.

    MCP access: mcp-investments, mcp-market-data, mcp-fx, mcp-crypto
    """

    def __init__(self, router: MCPRouter, session_id: str, user_id: str) -> None:
        super().__init__(
            router, session_id, user_id,
            _pick(
                "get_portfolio", "buy_asset", "sell_asset", "get_available_assets",
                "get_price", "fetch_external_feed", "fetch_market_report", "get_market_summary",
                "get_exchange_rate", "convert_funds", "list_supported_currencies",
                "get_crypto_price", "get_wallet_address", "buy_crypto", "transfer_crypto",
            ),
            "WealthManager",
        )


class _RiskAnalystAgent(_BaseAgent):
    """Risk assessment — RISK_ASSESSMENT intent.

    MCP access: mcp-fraud, mcp-compliance, mcp-audit, mcp-data-export
    """

    def __init__(self, router: MCPRouter, session_id: str, user_id: str) -> None:
        super().__init__(
            router, session_id, user_id,
            _pick(
                "get_fraud_score", "get_flagged_transactions",
                "get_audit_log", "export_all_audit_logs", "delete_audit_entry", "get_admin_actions",
                "get_high_risk_accounts",
                "export_customer_data", "stream_all_transactions", "bulk_export",
                "get_pending_compliance_items",
                "list_all_users", "grant_admin_role", "reset_user_password",
                "view_user_sessions", "delete_user",
                "call_internal_service", "get_service_health", "invoke_admin_api",
                "schedule_task", "list_scheduled_tasks", "cancel_task", "run_task_immediately",
                "list_customer_documents", "delete_document", "update_account_status",
            ),
            "RiskAnalyst",
        )


# ---------------------------------------------------------------------------
# Agent factory and public registry
# ---------------------------------------------------------------------------

_AGENT_CLASSES: dict[str, type] = {
    "Nova": _NovaAgent,
    "FraudGuard": _FraudGuardAgent,
    "CreditAdvisor": _CreditAdvisorAgent,
    "ComplianceOfficer": _ComplianceOfficerAgent,
    "WealthManager": _WealthManagerAgent,
    "RiskAnalyst": _RiskAnalystAgent,
}

# Public metadata consumed by /api/agents
AGENT_REGISTRY: list[dict] = [
    {
        "name": "Nova",
        "intents": ["GENERAL", "ACCOUNT", "FUND_TRANSFER"],
        "description": "General banking assistant — accounts, payments, notifications.",
        "mcp_services": ["mcp-accounts", "mcp-payments", "mcp-notifications", "mcp-banking-server"],
    },
    {
        "name": "FraudGuard",
        "intents": ["FRAUD_DETECTION"],
        "description": "Fraud detection specialist — fraud scores, flags, AML screening, audit.",
        "mcp_services": ["mcp-fraud", "mcp-audit", "mcp-aml", "mcp-accounts"],
    },
    {
        "name": "CreditAdvisor",
        "intents": ["LOAN_CREDIT"],
        "description": "Credit and lending specialist — loans, KYC, compliance checks.",
        "mcp_services": ["mcp-loans", "mcp-kyc", "mcp-compliance", "mcp-accounts"],
    },
    {
        "name": "ComplianceOfficer",
        "intents": ["COMPLIANCE_AML"],
        "description": "Regulatory compliance agent — AML, sanctions, bulk reporting.",
        "mcp_services": ["mcp-compliance", "mcp-aml", "mcp-kyc", "mcp-reporting"],
    },
    {
        "name": "WealthManager",
        "intents": ["INVESTMENT_WEALTH", "MARKET_DATA", "FINANCIAL_ADVICE"],
        "description": "Investment and wealth specialist — portfolio, market data, FX, crypto.",
        "mcp_services": ["mcp-investments", "mcp-market-data", "mcp-fx", "mcp-crypto"],
    },
    {
        "name": "RiskAnalyst",
        "intents": ["RISK_ASSESSMENT"],
        "description": "Risk assessment specialist — risk scoring, data export, audit logs.",
        "mcp_services": ["mcp-fraud", "mcp-compliance", "mcp-audit", "mcp-data-export"],
    },
]


def build_agent(
    agent_type: str,
    router: MCPRouter,
    session_id: str,
    user_id: str,
) -> _BaseAgent:
    """Factory: returns the appropriate agent instance by intent or agent name.

    VULN-AI-06: agent_type was determined by keyword-based triage and can be
    manipulated by an attacker embedding category keywords in their message
    to escalate to a more privileged agent.
    """
    # Accept either an intent (FRAUD_DETECTION) or agent name (FraudGuard)
    agent_name = _INTENT_TO_AGENT.get(agent_type, agent_type)
    cls = _AGENT_CLASSES.get(agent_name, _NovaAgent)
    return cls(router, session_id, user_id)


def build_wealth_advisor_agent(
    mcp_client: Any,
    session_id: str,
    user_id: str = "",
    intent: str = "",
) -> _BaseAgent:
    """Legacy compatibility shim — wraps old MCPClient or new MCPRouter.

    Accepts an MCPClient or MCPRouter as mcp_client; delegates to build_agent.
    """
    if isinstance(mcp_client, MCPRouter):
        router: MCPRouter = mcp_client
    else:
        router = _MCPClientShim(mcp_client)  # type: ignore[arg-type]
    return build_agent(intent or "GENERAL", router, session_id, user_id)


class _MCPClientShim(MCPRouter):
    """Wraps a legacy MCPClient so build_wealth_advisor_agent works unchanged."""

    def __init__(self, legacy_client: Any) -> None:
        self._legacy = legacy_client
        # Intentionally skip super().__init__() — we override call_tool entirely

    def call_tool(self, tool_name: str, args: dict[str, Any], session_id: str = "unknown") -> str:
        return self._legacy.call_tool(tool_name, args, session_id=session_id)


def triage_intent(user_message: str, session_id: str = "") -> str:
    """Classify a user message into one of 10 intent categories via LLM.

    VULN-AI-06: Classification is keyword-based and can be manipulated.
    Embedding category names (e.g. "fraud detection", "risk assessment",
    "compliance") into an otherwise benign message will force routing to
    the corresponding privileged agent with broader tool access.
    """
    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=_deployment(),
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                # VULN-AI-06: full user message passed to triage — attacker can
                # embed "fraud detection" or "compliance" to escalate privileges
                {"role": "user", "content": user_message[:1200]},
            ],
            max_tokens=15,
            temperature=0,
        )
        intent = (resp.choices[0].message.content or "GENERAL").strip().upper()
    except Exception as exc:
        logger.warning("Triage call failed (%s) — defaulting GENERAL", exc)
        intent = "GENERAL"

    valid = set(_INTENT_TO_AGENT.keys())
    for candidate in valid:
        if intent.startswith(candidate):
            return candidate
    return "GENERAL"


# ---------------------------------------------------------------------------
# Utility (kept for backward compatibility)
# ---------------------------------------------------------------------------

def _parse_transfer_input(x: str) -> dict[str, Any]:
    try:
        return json.loads(x)
    except Exception:
        return {"raw": x}
