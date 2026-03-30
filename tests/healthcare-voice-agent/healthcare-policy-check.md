# NuGuard Policy Report

**Generated:** 2026-03-30T21:31:40+00:00  
**LLM:** not used  

**13 gap(s)**

## [MEDIUM] CHECK-001: HITL Enforcement

_Policy defines human-in-the-loop triggers. The SBOM should contain GUARDRAIL nodes (e.g. InputGuardrail, OutputGuardrail) or PROMPT nodes with explicit escalation instructions to enforce them._

**Section:** hitl_triggers

The policy defines HITL triggers but the SBOM contains no GUARDRAIL nodes that could enforce them. Found 2 PROMPT node(s) with related content (prompt-level instructions are weaker than guardrail enforcement).

**Searched:**
- GUARDRAIL nodes: none found in SBOM (30 total nodes)
- PROMPT nodes: found 2 prompt(s) with related content

**Prompt evidence (partial):**
- 'System Instruction': '…You are a helpful AI Medical Triage Assistant. Your goal is to gather a clear list of symptoms from the patient…' [matched: medical, symptoms, patient]
- 'System Message': '…You are an intelligent medical assistant that triages patients.…' [matched: medical, patient]

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Access patient-details or medical-history records for any user other than the authenticated patient' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 1 TOOL nodes: search

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Create an appointment or payment flow without confirmed patient identity and selected slot' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 1 TOOL nodes: search

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Present prognosis output as a diagnosis or omit the medical disclaimer' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 1 TOOL nodes: search

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'generic' has no rate_limit attribute in the SBOM.

**Component:** generic

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'generic': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'health_check' has no rate_limit attribute in the SBOM.

**Component:** health_check

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'health_check': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'serve_spa' has no rate_limit attribute in the SBOM.

**Component:** serve_spa

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'serve_spa': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_medical_history' has no rate_limit attribute in the SBOM.

**Component:** get_medical_history

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_medical_history': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_details' has no rate_limit attribute in the SBOM.

**Component:** get_patient_details

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_patient_details': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'create_appointment' has no rate_limit attribute in the SBOM.

**Component:** create_appointment

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'create_appointment': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'login' has no rate_limit attribute in the SBOM.

**Component:** login

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'login': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'normalize' has no rate_limit attribute in the SBOM.

**Component:** normalize

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'normalize': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_langgraph' has no rate_limit attribute in the SBOM.

**Component:** run_langgraph

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'run_langgraph': not set
