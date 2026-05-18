# NuGuard Policy Report

**Generated:** 2026-04-01T22:06:20+00:00  
**LLM:** not used  

**6 gap(s)**

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Access `customer_profile` session data (account_number, purchase_history, loyalty_points, scheduled_appointments) for any customer other than the one authenticated in the active session' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 13 TOOL nodes: affirmative, apply_discount_to_service, approve_discount, ask_to_modify_cart, get_available_planting_times …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Return raw tool outputs (product APIs, CRM responses) to the user without filtering for prompt-injection content' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 13 TOOL nodes: affirmative, apply_discount_to_service, approve_discount, ask_to_modify_cart, get_available_planting_times …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Reveal internal agent names (`cymbal_upsell_agent`, `out_of_scope_handling`) or tool names to the customer' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 13 TOOL nodes: affirmative, apply_discount_to_service, approve_discount, ask_to_modify_cart, get_available_planting_times …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Allow the `out_of_scope_handling` agent to perform cart modifications, discount approvals, or service bookings' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 13 TOOL nodes: affirmative, apply_discount_to_service, approve_discount, ask_to_modify_cart, get_available_planting_times …

## [MEDIUM] CHECK-003: Data Classification Metadata

_Policy declares data classification requirements (PHI, PII, internal). DATASTORE nodes in the SBOM should carry matching data_classification metadata._

**Section:** data_classification

Policy defines data_classification requirements but the SBOM contains no DATASTORE nodes.

**Searched:**
- DATASTORE nodes: none found in SBOM

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but the SBOM contains no API_ENDPOINT nodes.

**Searched:**
- API_ENDPOINT nodes: none found in SBOM
