"""
MCP Router — maps tool names to MCP server URLs.
VULN: Single router with no per-user or per-agent authorization.
Any agent can call any tool regardless of intended scope.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger("orchestrator.mcp_router")

# Map: tool_name → (service_name, env_var_for_url, default_url)
_TOOL_REGISTRY: dict[str, tuple[str, str, str]] = {
    # mcp-accounts
    "get_account": ("mcp-accounts", "MCP_ACCOUNTS_URL", "http://mcp-accounts:8080"),
    "list_all_accounts": ("mcp-accounts", "MCP_ACCOUNTS_URL", "http://mcp-accounts:8080"),
    "update_account_status": ("mcp-accounts", "MCP_ACCOUNTS_URL", "http://mcp-accounts:8080"),
    # mcp-payments
    "initiate_payment": ("mcp-payments", "MCP_PAYMENTS_URL", "http://mcp-payments:8080"),
    "get_payment_status": ("mcp-payments", "MCP_PAYMENTS_URL", "http://mcp-payments:8080"),
    "cancel_payment": ("mcp-payments", "MCP_PAYMENTS_URL", "http://mcp-payments:8080"),
    # mcp-cards
    "get_card_details": ("mcp-cards", "MCP_CARDS_URL", "http://mcp-cards:8080"),
    "get_card_transactions": ("mcp-cards", "MCP_CARDS_URL", "http://mcp-cards:8080"),
    "freeze_card": ("mcp-cards", "MCP_CARDS_URL", "http://mcp-cards:8080"),
    "unfreeze_card": ("mcp-cards", "MCP_CARDS_URL", "http://mcp-cards:8080"),
    # mcp-loans
    "apply_for_loan": ("mcp-loans", "MCP_LOANS_URL", "http://mcp-loans:8080"),
    "get_loan_details": ("mcp-loans", "MCP_LOANS_URL", "http://mcp-loans:8080"),
    "approve_loan": ("mcp-loans", "MCP_LOANS_URL", "http://mcp-loans:8080"),
    "reject_loan": ("mcp-loans", "MCP_LOANS_URL", "http://mcp-loans:8080"),
    # mcp-investments
    "get_portfolio": ("mcp-investments", "MCP_INVESTMENTS_URL", "http://mcp-investments:8080"),
    "buy_asset": ("mcp-investments", "MCP_INVESTMENTS_URL", "http://mcp-investments:8080"),
    "sell_asset": ("mcp-investments", "MCP_INVESTMENTS_URL", "http://mcp-investments:8080"),
    "get_available_assets": ("mcp-investments", "MCP_INVESTMENTS_URL", "http://mcp-investments:8080"),
    # mcp-market-data
    "get_price": ("mcp-market-data", "MCP_MARKET_DATA_URL", "http://mcp-market-data:8080"),
    "fetch_external_feed": ("mcp-market-data", "MCP_MARKET_DATA_URL", "http://mcp-market-data:8080"),
    "get_market_summary": ("mcp-market-data", "MCP_MARKET_DATA_URL", "http://mcp-market-data:8080"),
    # mcp-fx
    "get_exchange_rate": ("mcp-fx", "MCP_FX_URL", "http://mcp-fx:8080"),
    "convert_funds": ("mcp-fx", "MCP_FX_URL", "http://mcp-fx:8080"),
    "list_supported_currencies": ("mcp-fx", "MCP_FX_URL", "http://mcp-fx:8080"),
    # mcp-crypto
    "get_crypto_price": ("mcp-crypto", "MCP_CRYPTO_URL", "http://mcp-crypto:8080"),
    "get_wallet_address": ("mcp-crypto", "MCP_CRYPTO_URL", "http://mcp-crypto:8080"),
    "buy_crypto": ("mcp-crypto", "MCP_CRYPTO_URL", "http://mcp-crypto:8080"),
    "transfer_crypto": ("mcp-crypto", "MCP_CRYPTO_URL", "http://mcp-crypto:8080"),
    # mcp-fraud
    "get_fraud_score": ("mcp-fraud", "MCP_FRAUD_URL", "http://mcp-fraud:8080"),
    "flag_transaction": ("mcp-fraud", "MCP_FRAUD_URL", "http://mcp-fraud:8080"),
    "whitelist_account": ("mcp-fraud", "MCP_FRAUD_URL", "http://mcp-fraud:8080"),
    "get_flagged_transactions": ("mcp-fraud", "MCP_FRAUD_URL", "http://mcp-fraud:8080"),
    # mcp-kyc
    "get_kyc_status": ("mcp-kyc", "MCP_KYC_URL", "http://mcp-kyc:8080"),
    "submit_kyc_document": ("mcp-kyc", "MCP_KYC_URL", "http://mcp-kyc:8080"),
    "override_kyc": ("mcp-kyc", "MCP_KYC_URL", "http://mcp-kyc:8080"),
    "get_all_kyc_statuses": ("mcp-kyc", "MCP_KYC_URL", "http://mcp-kyc:8080"),
    # legacy mcp-banking-server
    "transfer_funds": ("mcp-banking-server", "MCP_SERVER_URL", "http://mcp-banking-server:8080"),
    "fetch_market_report": ("mcp-banking-server", "MCP_SERVER_URL", "http://mcp-banking-server:8080"),
    # mcp-aml
    "check_sanctions": ("mcp-aml", "MCP_AML_URL", "http://mcp-aml:8080"),
    "file_suspicious_activity_report": ("mcp-aml", "MCP_AML_URL", "http://mcp-aml:8080"),
    "waive_aml_check": ("mcp-aml", "MCP_AML_URL", "http://mcp-aml:8080"),
    "get_high_risk_accounts": ("mcp-aml", "MCP_AML_URL", "http://mcp-aml:8080"),
    # mcp-compliance
    "check_transaction_limits": ("mcp-compliance", "MCP_COMPLIANCE_URL", "http://mcp-compliance:8080"),
    "get_regulatory_requirements": ("mcp-compliance", "MCP_COMPLIANCE_URL", "http://mcp-compliance:8080"),
    "override_compliance": ("mcp-compliance", "MCP_COMPLIANCE_URL", "http://mcp-compliance:8080"),
    "get_pending_compliance_items": ("mcp-compliance", "MCP_COMPLIANCE_URL", "http://mcp-compliance:8080"),
    # mcp-notifications
    "send_alert": ("mcp-notifications", "MCP_NOTIFICATIONS_URL", "http://mcp-notifications:8080"),
    "send_otp": ("mcp-notifications", "MCP_NOTIFICATIONS_URL", "http://mcp-notifications:8080"),
    "broadcast_all_users": ("mcp-notifications", "MCP_NOTIFICATIONS_URL", "http://mcp-notifications:8080"),
    "get_notification_history": ("mcp-notifications", "MCP_NOTIFICATIONS_URL", "http://mcp-notifications:8080"),
    # mcp-audit
    "get_audit_log": ("mcp-audit", "MCP_AUDIT_URL", "http://mcp-audit:8080"),
    "export_all_audit_logs": ("mcp-audit", "MCP_AUDIT_URL", "http://mcp-audit:8080"),
    "delete_audit_entry": ("mcp-audit", "MCP_AUDIT_URL", "http://mcp-audit:8080"),
    "get_admin_actions": ("mcp-audit", "MCP_AUDIT_URL", "http://mcp-audit:8080"),
    # mcp-reporting
    "generate_report": ("mcp-reporting", "MCP_REPORTING_URL", "http://mcp-reporting:8080"),
    "get_customer_summary": ("mcp-reporting", "MCP_REPORTING_URL", "http://mcp-reporting:8080"),
    "bulk_export_all_customers": ("mcp-reporting", "MCP_REPORTING_URL", "http://mcp-reporting:8080"),
    "get_regulatory_report": ("mcp-reporting", "MCP_REPORTING_URL", "http://mcp-reporting:8080"),
    # mcp-documents
    "get_document": ("mcp-documents", "MCP_DOCUMENTS_URL", "http://mcp-documents:8080"),
    "list_customer_documents": ("mcp-documents", "MCP_DOCUMENTS_URL", "http://mcp-documents:8080"),
    "create_document": ("mcp-documents", "MCP_DOCUMENTS_URL", "http://mcp-documents:8080"),
    "delete_document": ("mcp-documents", "MCP_DOCUMENTS_URL", "http://mcp-documents:8080"),
    # mcp-admin
    "list_all_users": ("mcp-admin", "MCP_ADMIN_URL", "http://mcp-admin:8080"),
    "grant_admin_role": ("mcp-admin", "MCP_ADMIN_URL", "http://mcp-admin:8080"),
    "reset_user_password": ("mcp-admin", "MCP_ADMIN_URL", "http://mcp-admin:8080"),
    "view_user_sessions": ("mcp-admin", "MCP_ADMIN_URL", "http://mcp-admin:8080"),
    "delete_user": ("mcp-admin", "MCP_ADMIN_URL", "http://mcp-admin:8080"),
    # mcp-data-export
    "export_customer_data": ("mcp-data-export", "MCP_DATA_EXPORT_URL", "http://mcp-data-export:8080"),
    "stream_all_transactions": ("mcp-data-export", "MCP_DATA_EXPORT_URL", "http://mcp-data-export:8080"),
    "bulk_export": ("mcp-data-export", "MCP_DATA_EXPORT_URL", "http://mcp-data-export:8080"),
    # mcp-internal-bridge
    "call_internal_service": ("mcp-internal-bridge", "MCP_INTERNAL_BRIDGE_URL", "http://mcp-internal-bridge:8080"),
    "get_service_health": ("mcp-internal-bridge", "MCP_INTERNAL_BRIDGE_URL", "http://mcp-internal-bridge:8080"),
    "invoke_admin_api": ("mcp-internal-bridge", "MCP_INTERNAL_BRIDGE_URL", "http://mcp-internal-bridge:8080"),
    # mcp-scheduler
    "schedule_task": ("mcp-scheduler", "MCP_SCHEDULER_URL", "http://mcp-scheduler:8080"),
    "list_scheduled_tasks": ("mcp-scheduler", "MCP_SCHEDULER_URL", "http://mcp-scheduler:8080"),
    "cancel_task": ("mcp-scheduler", "MCP_SCHEDULER_URL", "http://mcp-scheduler:8080"),
    "run_task_immediately": ("mcp-scheduler", "MCP_SCHEDULER_URL", "http://mcp-scheduler:8080"),
}


class MCPRouter:
    """Routes tool calls to the correct MCP microservice.

    VULN: No per-agent or per-user authorization. Any agent calling any tool
    will succeed as long as the underlying service accepts the request.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def call_tool(self, tool_name: str, args: dict[str, Any], session_id: str = "unknown") -> str:
        """Route tool_name to the correct MCP service and return result as string."""
        entry = _TOOL_REGISTRY.get(tool_name)
        if not entry:
            return f"Error: unknown tool '{tool_name}'"

        service_name, env_var, default_url = entry
        base_url = os.getenv(env_var, default_url)
        tools_url = f"{base_url.rstrip('/')}/tools/call"

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        }

        logger.info("MCPRouter: tool=%s service=%s session=%s", tool_name, service_name, session_id)

        try:
            resp = self._session.post(tools_url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("result", {}).get("content", [])
            if content:
                return content[0].get("text", str(data))
            return str(data.get("result", data))
        except requests.RequestException as exc:
            logger.warning("MCPRouter: tool=%s service=%s FAILED: %s", tool_name, service_name, exc)
            return f"Service {service_name} unavailable: {exc}"
