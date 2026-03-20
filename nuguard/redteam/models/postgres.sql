-- No projects table in v1; test_name slug denormalized directly onto each record.
-- JSONB columns reference JSON Schema files in redteam-service/schema/ for validation.

CREATE TABLE redteam_sboms (
  id TEXT PRIMARY KEY,
  test_name TEXT NOT NULL,
  sbom JSONB NOT NULL,          -- validated by schema/sbom.schema.json
  graph_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE cognitive_policies (
  id TEXT PRIMARY KEY,
  test_name TEXT NOT NULL,
  policy_md TEXT NOT NULL,
  policy_parsed JSONB,          -- validated by schema/cognitive-policy-parsed.schema.json
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE redteam_tests (
  id TEXT PRIMARY KEY,
  test_name TEXT NOT NULL,
  sbom_id TEXT REFERENCES redteam_sboms(id),
  policy_id TEXT REFERENCES cognitive_policies(id),
  status TEXT NOT NULL DEFAULT 'pending',
  config JSONB NOT NULL,        -- validated by schema/test-config.schema.json
  risk_score FLOAT,
  summary JSONB,                -- validated by schema/test-summary.schema.json
  created_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE redteam_findings (
  id TEXT PRIMARY KEY,
  test_id TEXT REFERENCES redteam_tests(id),
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  exploit_chain_id TEXT,
  policy_violation_type TEXT,
  primary_compliance_ref TEXT,   -- first compliance_refs entry, e.g. 'owasp_llm_top10:LLM01'; full list in details JSONB
  evidence_trace_hash TEXT,
  log_correlation JSONB,        -- validated by schema/log-correlation.schema.json
  details JSONB NOT NULL         -- validated by schema/redteam-finding.schema.json
);

CREATE TABLE attack_graphs (
  id TEXT PRIMARY KEY,
  test_id TEXT REFERENCES redteam_tests(id),
  test_name TEXT NOT NULL,
  graph JSONB NOT NULL,         -- validated by schema/attack-graph.schema.json
  node_count INT,
  edge_count INT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE exploit_chains (
  id TEXT PRIMARY KEY,
  test_id TEXT REFERENCES redteam_tests(id),
  graph_id TEXT REFERENCES attack_graphs(id),
  chain JSONB NOT NULL,         -- validated by schema/exploit-chain.schema.json
  exploit_risk FLOAT,
  step_count INT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);