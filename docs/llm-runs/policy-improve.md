No blocking clarification needed. I reviewed [openai-cs-policy-check.md](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/reports/openai-cs-policy-check.md:1), the policy, SBOM, and the FastAPI source.

**Key Accuracy Issues**

1. **Restricted-action matching is too literal.**  
   The report says “access booking records…” does not match any tool, but the SBOM has `Lookup Reservation`, which is clearly relevant to booking-record access. That tool maps to [main.py:87](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:87).  
   Better behavior: match policy actions to tool descriptions, metadata, data classes, and source behavior, not just tool names.

2. **Some restricted actions may be absent capabilities, not gaps.**  
   “Create or modify user accounts,” “execute code,” and “export booking data” may not correspond to any implemented tool. The report currently treats that as a medium gap, but for engineers this should be split into:
   - `Capability present but not policy-mapped`
   - `Capability absent / not applicable`
   - `Capability unknown`

3. **`Generic` API endpoint is likely a false positive.**  
   The SBOM metadata says this node was LLM-soft-rejected as a false positive, but the policy report still flags it for rate limiting. The report should either suppress soft-rejected nodes or show `confidence`, `reason`, and `why included`.

4. **Rate-limit severity is too flat.**  
   All endpoint rate-limit gaps are `LOW`, but `/login` and `/chat` deserve higher priority than `/logout`. `/login` is brute-force sensitive, and `/chat` is cost/abuse/agent-execution sensitive. See [api.py:145](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/api.py:145) and [api.py:224](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/api.py:224).

5. **Findings lack route/tool evidence.**  
   The report names components like `Me`, `Login`, and `Chat Endpoint`, but does not show method/path/source line. For security engineers, `POST /chat at api.py:224` is much more actionable than `Chat Endpoint`.

**Explainability Improvements**

- Add a short **Policy Coverage Summary**:
  - Restricted actions: `5 total`, `1 likely mapped`, `3 absent/unknown`, `1 needs workflow mapping`
  - Rate limits: `5 endpoints checked`, `0 instrumented`, `1 false-positive candidate`
  - HITL triggers/data classification: `not checked in this report` or `checked elsewhere`

- For each restricted action, show a mapping table:
  - Policy control ID
  - Restricted action
  - Candidate tools/endpoints
  - Match reason
  - Confidence
  - Status

  Example:
  `CTRL-012 | access other users' booking records | Lookup Reservation | reads reservation PII from DB | high | needs ownership enforcement evidence`

- Explain the difference between **policy coverage** and **policy enforcement**.  
  Matching a policy action to a tool only proves the checker found the right enforcement boundary. It does not prove the app enforces the rule.

- Include “why this matters” in engineer language:
  - For AI Engineer: “This tool can place booking data into model context.”
  - For Security Engineer: “This is an authorization/IDOR control boundary.”

**Actionable Intelligence Improvements**

- For `Lookup Reservation`, recommend concrete checks:
  - Verify reservation lookup is bound to authenticated session identity.
  - Reject confirmation numbers that do not belong to the session user.
  - Log lookup attempts with user, confirmation number, decision, and agent/tool name.
  - Add negative tests for cross-user booking access.

- For `Cancel Flight`, map it to the refund/cancellation workflow policy.  
  Even if the policy text says “issue refunds or credits,” the relevant implemented boundary is probably [main.py:337](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:337).

- For rate limits, give endpoint-specific remediation:
  - `POST /login`: stricter IP/account throttling and lockout/backoff.
  - `POST /chat`: per-user/session rate limit plus token/cost budget.
  - `GET /me`: moderate authenticated request throttling.
  - `POST /logout`: low priority.

- Avoid recommending “rename the tool” as the primary remediation.  
  That is implementation noise. Better: add explicit policy metadata to the SBOM/tool, such as:
  - `policy_controls: ["CTRL-012"]`
  - `data_access: ["booking_record", "PII"]`
  - `operation: "read_sensitive"`
  - `authz_required: "session_owner"`

**Best Next Improvements To The Report Generator**

- Suppress or downgrade SBOM nodes marked `llm_soft_rejected`.
- Add semantic matching between policy actions and tool descriptions/source behavior.
- Add `N/A` and `UNKNOWN` statuses instead of reporting every non-match as a gap.
- Include source file, route, method, and line number in every finding.
- Prioritize rate-limit findings by endpoint risk.
- Add a “likely false positives / needs validation” section.

Optional clarification: do you want this as a report-only critique, or should I next make the minimum code/report-generator changes to improve the policy check output?