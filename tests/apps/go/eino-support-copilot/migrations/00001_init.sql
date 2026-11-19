-- +goose Up
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE demo_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  role text NOT NULL CONSTRAINT demo_users_role_valid CHECK (role IN ('customer', 'agent', 'admin')),
  display_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id uuid NOT NULL REFERENCES demo_users(id),
  title text NOT NULL DEFAULT 'New conversation',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_customer_idx ON conversations(customer_id, updated_at DESC);

CREATE TABLE messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role text NOT NULL CONSTRAINT messages_role_valid CHECK (role IN ('user', 'assistant', 'agent')),
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_idx ON messages(conversation_id, created_at, id);

CREATE TABLE tickets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id),
  status text NOT NULL CONSTRAINT tickets_status_valid CHECK (
    status IN ('open', 'triaged', 'waiting_review', 'resolved', 'escalated')
  ),
  priority text NOT NULL CONSTRAINT tickets_priority_valid CHECK (priority IN ('P0', 'P1', 'P2', 'P3')),
  intent text NOT NULL,
  summary text NOT NULL,
  draft jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tickets_queue_idx ON tickets(status, priority, created_at);
CREATE UNIQUE INDEX tickets_one_active_per_conversation_idx
  ON tickets(conversation_id)
  WHERE status IN ('open', 'triaged', 'waiting_review');

CREATE TABLE ticket_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id uuid NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  reviewer_id uuid NOT NULL REFERENCES demo_users(id),
  action text NOT NULL CONSTRAINT ticket_reviews_action_valid CHECK (
    action IN ('approve', 'edit_and_approve', 'escalate')
  ),
  final_answer text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ticket_reviews_ticket_idx ON ticket_reviews(ticket_id, created_at, id);

CREATE TABLE knowledge_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  filename text NOT NULL,
  media_type text NOT NULL,
  sha256 text NOT NULL UNIQUE,
  status text NOT NULL CONSTRAINT knowledge_documents_status_valid CHECK (
    status IN ('uploaded', 'indexing', 'ready', 'failed')
  ),
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE knowledge_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  ordinal integer NOT NULL CONSTRAINT knowledge_chunks_ordinal_nonnegative CHECK (ordinal >= 0),
  content text NOT NULL,
  locator text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  embedding vector(1536) NOT NULL,
  UNIQUE(document_id, ordinal)
);
CREATE INDEX knowledge_chunks_fts_idx ON knowledge_chunks USING gin(search_vector);
CREATE INDEX knowledge_chunks_vector_idx ON knowledge_chunks USING hnsw(embedding vector_cosine_ops);

CREATE TABLE agent_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id),
  message_id uuid REFERENCES messages(id),
  ticket_id uuid REFERENCES tickets(id),
  status text NOT NULL CONSTRAINT agent_runs_status_valid CHECK (
    status IN ('queued', 'running', 'waiting_review', 'completed', 'failed', 'cancelled')
  ),
  mode text NOT NULL CONSTRAINT agent_runs_mode_valid CHECK (mode IN ('mock', 'openai-compatible')),
  model_name text,
  input_summary text NOT NULL,
  output_summary text,
  usage jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_code text,
  error_message text,
  replayed_from_run_id uuid REFERENCES agent_runs(id),
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_runs_conversation_idx ON agent_runs(conversation_id, created_at DESC);
CREATE INDEX agent_runs_status_idx ON agent_runs(status, created_at DESC);

CREATE TABLE agent_run_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  sequence integer NOT NULL CONSTRAINT agent_run_steps_sequence_nonnegative CHECK (sequence >= 0),
  event_type text NOT NULL,
  stage text,
  input_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  tool_name text,
  tool_arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  duration_ms bigint CONSTRAINT agent_run_steps_duration_nonnegative CHECK (duration_ms IS NULL OR duration_ms >= 0),
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id, sequence)
);
CREATE INDEX agent_run_steps_run_idx ON agent_run_steps(run_id, sequence);

CREATE TABLE eval_datasets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  version text NOT NULL,
  dataset_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(name, version),
  UNIQUE(dataset_hash)
);

CREATE TABLE eval_cases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES eval_datasets(id) ON DELETE CASCADE,
  case_key text NOT NULL,
  input text NOT NULL,
  expected_intent text NOT NULL,
  expected_priority text NOT NULL CONSTRAINT eval_cases_priority_valid CHECK (
    expected_priority IN ('P0', 'P1', 'P2', 'P3')
  ),
  expected_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
  expected_citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  expected_requires_review boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(dataset_id, case_key)
);

CREATE TABLE eval_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid NOT NULL REFERENCES eval_datasets(id),
  status text NOT NULL CONSTRAINT eval_runs_status_valid CHECK (
    status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
  ),
  mode text NOT NULL CONSTRAINT eval_runs_mode_valid CHECK (mode IN ('mock', 'openai-compatible')),
  commit_sha text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  eval_run_id uuid NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  eval_case_id uuid NOT NULL REFERENCES eval_cases(id),
  passed boolean NOT NULL,
  scores jsonb NOT NULL DEFAULT '{}'::jsonb,
  actual jsonb NOT NULL DEFAULT '{}'::jsonb,
  latency_ms bigint NOT NULL CONSTRAINT eval_results_latency_nonnegative CHECK (latency_ms >= 0),
  error_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(eval_run_id, eval_case_id)
);

CREATE TABLE mock_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id text NOT NULL UNIQUE,
  plan text NOT NULL,
  account_status text NOT NULL CONSTRAINT mock_accounts_status_valid CHECK (
    account_status IN ('active', 'past_due', 'suspended', 'closed')
  ),
  subscription_sync_status text NOT NULL CONSTRAINT mock_accounts_sync_status_valid CHECK (
    subscription_sync_status IN ('pending', 'processing', 'active', 'failed')
  ),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mock_payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id text NOT NULL UNIQUE,
  account_id text NOT NULL REFERENCES mock_accounts(account_id),
  status text NOT NULL CONSTRAINT mock_payments_status_valid CHECK (
    status IN ('pending', 'paid', 'failed', 'refunded')
  ),
  amount_cents bigint NOT NULL CONSTRAINT mock_payments_amount_nonnegative CHECK (amount_cents >= 0),
  currency text NOT NULL CONSTRAINT mock_payments_currency_iso CHECK (char_length(currency) = 3),
  paid_at timestamptz,
  linked_subscription text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mock_payments_account_idx ON mock_payments(account_id, created_at DESC);

CREATE TABLE mock_service_incidents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  service text NOT NULL,
  status text NOT NULL CONSTRAINT mock_service_incidents_status_valid CHECK (
    status IN ('operational', 'degraded', 'outage')
  ),
  impact_scope text NOT NULL,
  summary text NOT NULL,
  started_at timestamptz NOT NULL,
  resolved_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mock_service_incidents_service_idx ON mock_service_incidents(service, updated_at DESC);

-- +goose Down
DROP TABLE IF EXISTS mock_service_incidents;
DROP TABLE IF EXISTS mock_payments;
DROP TABLE IF EXISTS mock_accounts;
DROP TABLE IF EXISTS eval_results;
DROP TABLE IF EXISTS eval_runs;
DROP TABLE IF EXISTS eval_cases;
DROP TABLE IF EXISTS eval_datasets;
DROP TABLE IF EXISTS agent_run_steps;
DROP TABLE IF EXISTS agent_runs;
DROP TABLE IF EXISTS knowledge_chunks;
DROP TABLE IF EXISTS knowledge_documents;
DROP TABLE IF EXISTS ticket_reviews;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS demo_users;
