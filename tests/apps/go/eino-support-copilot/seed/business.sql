-- Generated demonstration data only. No production or employer data is included.
INSERT INTO demo_users (id, role, display_name, created_at)
VALUES
  ('40000000-0000-0000-0000-000000000001', 'customer', 'Ada Demo', '2026-08-13T08:00:00Z'),
  ('40000000-0000-0000-0000-000000000002', 'agent', 'Lin Demo Agent', '2026-08-13T08:00:00Z')
ON CONFLICT (id) DO UPDATE SET
  role = EXCLUDED.role,
  display_name = EXCLUDED.display_name;

INSERT INTO mock_accounts (id, account_id, plan, account_status, subscription_sync_status, updated_at)
VALUES
  ('10000000-0000-0000-0000-000000000001', 'acct_demo_pro', 'pro', 'active', 'processing', '2026-08-13T09:00:00Z'),
  ('10000000-0000-0000-0000-000000000002', 'acct_demo_past_due', 'starter', 'past_due', 'failed', '2026-08-13T08:30:00Z')
ON CONFLICT (account_id) DO UPDATE SET
  plan = EXCLUDED.plan,
  account_status = EXCLUDED.account_status,
  subscription_sync_status = EXCLUDED.subscription_sync_status,
  updated_at = EXCLUDED.updated_at;

INSERT INTO mock_payments (
  id, payment_id, account_id, status, amount_cents, currency, paid_at,
  linked_subscription, created_at
)
VALUES
  (
    '20000000-0000-0000-0000-000000000001', 'pay_demo_paid', 'acct_demo_pro',
    'paid', 2900, 'USD', '2026-08-13T08:55:00Z', 'pro', '2026-08-13T08:54:00Z'
  ),
  (
    '20000000-0000-0000-0000-000000000002', 'pay_demo_failed', 'acct_demo_past_due',
    'failed', 900, 'USD', NULL, 'starter', '2026-08-13T08:25:00Z'
  )
ON CONFLICT (payment_id) DO UPDATE SET
  account_id = EXCLUDED.account_id,
  status = EXCLUDED.status,
  amount_cents = EXCLUDED.amount_cents,
  currency = EXCLUDED.currency,
  paid_at = EXCLUDED.paid_at,
  linked_subscription = EXCLUDED.linked_subscription,
  created_at = EXCLUDED.created_at;

INSERT INTO mock_service_incidents (
  id, service, status, impact_scope, summary, started_at, resolved_at, updated_at
)
VALUES
  (
    '30000000-0000-0000-0000-000000000001', 'subscription-sync', 'degraded',
    'Some plan upgrades may take up to 15 minutes to appear.',
    'A generated queue backlog is delaying subscription synchronization.',
    '2026-08-13T08:40:00Z', NULL, '2026-08-13T09:05:00Z'
  ),
  (
    '30000000-0000-0000-0000-000000000002', 'checkout', 'operational',
    'No customer impact.', 'All generated checkout checks are healthy.',
    '2026-08-13T07:00:00Z', '2026-08-13T07:05:00Z', '2026-08-13T09:00:00Z'
  )
ON CONFLICT (id) DO UPDATE SET
  service = EXCLUDED.service,
  status = EXCLUDED.status,
  impact_scope = EXCLUDED.impact_scope,
  summary = EXCLUDED.summary,
  started_at = EXCLUDED.started_at,
  resolved_at = EXCLUDED.resolved_at,
  updated_at = EXCLUDED.updated_at;
