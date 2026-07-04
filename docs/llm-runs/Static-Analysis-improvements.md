Looking at the static analysis section of the report (tests/apps/openai-cs-agents-demo/reports/openai-cs-analysis.md), there are definitely some useful findings, but the overall presentation and some specific conclusions could be improved for clarity, accuracy, and actionability.

**Key Issues**

1. **HITL findings look materially over-reported.**  
   The report flags many tools as “irreversible” because of a matched pattern `rm`, including read-only tools like `faq_lookup_tool`, `baggage_tool`, `flight_status_tool`, and UI-only `display_seat_map`. In source, only `update_seat` and `cancel_flight` are clearly state-changing. See [main.py:151](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:151) and [main.py:337](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:337).  
   Recommendation: classify tools by behavior: `read-only`, `sensitive-read`, `state-changing`, `destructive`, instead of substring matching.

2. **Guardrail findings may be inaccurate or at least poorly explained.**  
   The report says some agent paths lack guardrails, but the source shows `input_guardrails` on specialist agents and the triage agent. See [main.py:302](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:302), [main.py:327](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:327), and [main.py:430](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:430).  
   Recommendation: distinguish “source has guardrails” from “SBOM graph lacks a `PROTECTS` edge.” If the extractor missed the relationship, report it as an SBOM modeling gap, not necessarily an app vulnerability.

3. **Dependency findings need stronger evidence and applicability.**  
   The Next.js findings are useful, but there are many of them and they should be grouped into one upgrade action. Python findings like `gunicorn .` lack a resolved installed version, which makes vulnerability matching look less reliable.  
   Recommendation: include manifest path, lockfile path, resolved version, vulnerable range, fixed version, and whether the vulnerable feature is likely reachable.

4. **The report mislabels non-CVE issues as CVEs.**  
   AI rule findings, supply-chain findings, and ATLAS findings are counted under “Unique CVEs.” That will confuse security engineers.  
   Recommendation: rename to “Unique findings” and split counts by category: dependency advisory, AI rule, supply-chain rule, MITRE ATLAS, scanner coverage gap.

5. **Several PASS results should be `N/A` or `Not evidenced`.**  
   Examples include Kubernetes NetworkPolicy, container resource limits, pinned container images, and datastore isolation. If no Kubernetes/container resources were analyzed, those should not be presented as passing controls.  
   Recommendation: add explicit statuses: `PASS`, `FAIL`, `WARNING`, `N/A`, `UNKNOWN`.

6. **Hosted model integrity guidance should be adjusted.**  
   The report recommends integrity verification for `gpt-4.1-mini`, but artifact hashing is not realistic for a hosted API model.  
   Recommendation: for hosted models, recommend provider/deployment pinning, model/version metadata, approved provider registry, evaluation gates, and change-control records.

7. **Scanner coverage gaps are underemphasized.**  
   `checkov` and `semgrep` were skipped. That means IaC and source-code security coverage is incomplete, but the report still reads like a comprehensive static analysis.  
   Recommendation: put a prominent “Partial coverage” note in the executive summary.

**Most Valuable Reporting Improvements**

- Add a short **Engineer Action Plan** near the top:
  - Upgrade `next` from `15.2.4`.
  - Resolve or pin Python dependency versions.
  - Add explicit HITL or confirmation controls for `cancel_flight` and `update_seat`.
  - Minimize or redact PII returned by `lookup_reservation`.
  - Install and run `semgrep` and `checkov`.

- Add source references for AI findings:
  - PII exposure through `lookup_reservation`: [main.py:87](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:87)
  - Seat update tool: [main.py:151](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:151)
  - Cancellation tool: [main.py:337](/workspaces/nuguard/tests/apps/openai-cs-agents-demo/python-backend/main.py:337)

- Add confidence/applicability labels:
  - `Confirmed`
  - `Likely`
  - `Needs validation`
  - `Extractor/modeling issue`

- Add a “Known False Positive Risks” section for the current HITL matcher, missing Python resolved versions, skipped scanners, and guardrail graph extraction.

Big picture: the report is directionally useful, especially on vulnerable dependencies and PII-to-model risk, but it currently inflates severity through noisy AI-rule findings and some PASS/FAIL ambiguity. For an AI Engineer or security engineer, the biggest improvement would be making each finding more evidence-backed, source-linked, and clearly actionable.