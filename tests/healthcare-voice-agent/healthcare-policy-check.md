# NuGuard Policy Report

**Generated:** 2026-03-29T05:15:57+00:00  
**LLM:** not used  

**14 finding(s)**

## [HIGH] Policy Gap: CHECK-001

**Section:** hitl_triggers

The policy defines HITL triggers but the SBOM contains no GUARDRAIL nodes that could enforce them.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Access patient-details or medical-history records for any user other than the authenticated patient' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Create an appointment or payment flow without confirmed patient identity and selected slot' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Present prognosis output as a diagnosis or omit the medical disclaimer' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'generic' has no rate_limit attribute in the SBOM.

**Component:** generic

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'Port 8080' has no rate_limit attribute in the SBOM.

**Component:** Port 8080

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'create_appointment' has no rate_limit attribute in the SBOM.

**Component:** create_appointment

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_medical_history' has no rate_limit attribute in the SBOM.

**Component:** get_medical_history

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_details' has no rate_limit attribute in the SBOM.

**Component:** get_patient_details

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'health_check' has no rate_limit attribute in the SBOM.

**Component:** health_check

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'login' has no rate_limit attribute in the SBOM.

**Component:** login

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'normalize' has no rate_limit attribute in the SBOM.

**Component:** normalize

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_langgraph' has no rate_limit attribute in the SBOM.

**Component:** run_langgraph

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'serve_spa' has no rate_limit attribute in the SBOM.

**Component:** serve_spa
