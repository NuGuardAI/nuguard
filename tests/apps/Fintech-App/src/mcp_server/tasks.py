"""
Celery task definitions for asynchronous fund transfers.

VULN-01 continuation: No ownership check is performed inside the task either.
The task blindly debits source_account and credits target_account.
"""
from __future__ import annotations

import logging
import os
import time
from celery import Celery

logger = logging.getLogger("mcp_server.tasks")

# ---------------------------------------------------------------------------
# Celery application
# NOTE: Uses REDIS_URL from env — should be treated as a secret at runtime
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "fintech_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=False,
    broker_connection_timeout=5,
)

# Azure Redis requires TLS — configure SSL certificate handling
if REDIS_URL.startswith("rediss://"):
    _ssl_opts = {"ssl_cert_reqs": None}
    celery_app.conf.broker_use_ssl = _ssl_opts
    celery_app.conf.redis_backend_use_ssl = _ssl_opts


# ---------------------------------------------------------------------------
# Transfer task
# ---------------------------------------------------------------------------
@celery_app.task(bind=True, name="fintech_tasks.execute_transfer", max_retries=3)
def execute_transfer(
    self,
    source_account_id: str,
    target_account_id: str,
    amount: float,
) -> dict:
    """Execute a funds transfer between two accounts.

    NOTE: No ownership validation — the task trusts whatever source_account_id
    was passed by the MCP tool, which itself reads from a global env var rather
    than the authenticated session context.  This is VULN-01.
    """
    try:
        logger.info(
            "Executing transfer: from=%s to=%s amount=%.2f",
            source_account_id,
            target_account_id,
            amount,
        )
        # Simulate a database write (in a real app this would hit a DB)
        time.sleep(0.1)  # Simulate I/O
        return {
            "status": "completed",
            "from_account": source_account_id,
            "to_account": target_account_id,
            "amount": amount,
            "currency": "USD",
            "transaction_ref": f"TXN-{int(time.time() * 1000)}",
        }
    except Exception as exc:
        logger.exception("Transfer task failed: %s", exc)
        raise self.retry(exc=exc, countdown=5)
