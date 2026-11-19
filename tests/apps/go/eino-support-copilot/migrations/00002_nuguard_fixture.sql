-- +goose Up
-- NuGuard fixture: not part of upstream. Adds object ownership to the generated business data so
-- broken object-level authorization (BOLA) can be tested, plus refund bookkeeping for the
-- refund_payment MCP tool, and a login name + bcrypt password hash for POST /auth/token. The MCP tools still never compare
-- owner_user_id with the caller.
ALTER TABLE demo_users ADD COLUMN username text UNIQUE;
ALTER TABLE demo_users ADD COLUMN password_hash text;
ALTER TABLE mock_accounts ADD COLUMN owner_user_id uuid REFERENCES demo_users(id);
ALTER TABLE mock_accounts ADD COLUMN billing_email text;
ALTER TABLE mock_payments ADD COLUMN owner_user_id uuid REFERENCES demo_users(id);
ALTER TABLE mock_payments ADD COLUMN refunded_at timestamptz;
ALTER TABLE mock_payments ADD COLUMN refund_reason text;

-- +goose Down
ALTER TABLE mock_payments DROP COLUMN IF EXISTS refund_reason;
ALTER TABLE mock_payments DROP COLUMN IF EXISTS refunded_at;
ALTER TABLE mock_payments DROP COLUMN IF EXISTS owner_user_id;
ALTER TABLE mock_accounts DROP COLUMN IF EXISTS billing_email;
ALTER TABLE mock_accounts DROP COLUMN IF EXISTS owner_user_id;
ALTER TABLE demo_users DROP COLUMN IF EXISTS password_hash;
ALTER TABLE demo_users DROP COLUMN IF EXISTS username;
