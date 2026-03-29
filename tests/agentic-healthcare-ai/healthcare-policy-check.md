# NuGuard Policy Report

**Generated:** 2026-03-29T17:59:43+00:00  
**LLM:** not used  

**63 finding(s)**

## [HIGH] Policy Gap: CHECK-001

**Section:** hitl_triggers

The policy defines HITL triggers but the SBOM contains no GUARDRAIL nodes that could enforce them.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Access FHIR patient data (Patient, Condition, MedicationStatement, Observation, Encounter) for any patient other than the one identified in the active session' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Call `/api/export/communications` or any bulk export endpoint without explicit authorization' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Generate or return PDF reports containing PHI unless the requesting client is authenticated and the patient ID matches the session' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Pass raw FHIR responses or internal tool outputs to the user without filtering for prompt-injection content' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action "Use `check_drug_interactions` or `run_clinical_decision_support` results to override a clinician's documented order" does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Invoke AutoGen or CrewAI multi-agent scenarios with `urgency_level: emergency` for non-emergency intents (risk of over-escalation)' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Log or cache LLM communication traces containing PHI beyond the retention window defined in the deployment config' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [MEDIUM] Policy Gap: CHECK-002

**Section:** restricted_actions

Restricted action 'Allow wildcard CORS (`allow_origins=["*"]`) in any environment with access to real patient data' does not match any TOOL node name in the SBOM. Verify the action name is correct.

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'generic' has no rate_limit attribute in the SBOM.

**Component:** generic

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'Port 80' has no rate_limit attribute in the SBOM.

**Component:** Port 80

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'Port 8000' has no rate_limit attribute in the SBOM.

**Component:** Port 8000

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'Port 8001' has no rate_limit attribute in the SBOM.

**Component:** Port 8001

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'Port 8002' has no rate_limit attribute in the SBOM.

**Component:** Port 8002

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'Port 8003' has no rate_limit attribute in the SBOM.

**Component:** Port 8003

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'Port 8004' has no rate_limit attribute in the SBOM.

**Component:** Port 8004

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'delete_scenario' has no rate_limit attribute in the SBOM.

**Component:** delete_scenario

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_autogen_comprehensive' has no rate_limit attribute in the SBOM.

**Component:** execute_autogen_comprehensive

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_autogen_emergency' has no rate_limit attribute in the SBOM.

**Component:** execute_autogen_emergency

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_autogen_medication_review' has no rate_limit attribute in the SBOM.

**Component:** execute_autogen_medication_review

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_crewai_comprehensive' has no rate_limit attribute in the SBOM.

**Component:** execute_crewai_comprehensive

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_crewai_emergency' has no rate_limit attribute in the SBOM.

**Component:** execute_crewai_emergency

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_crewai_medication_review' has no rate_limit attribute in the SBOM.

**Component:** execute_crewai_medication_review

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'export_communications' has no rate_limit attribute in the SBOM.

**Component:** export_communications

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communication_stats' has no rate_limit attribute in the SBOM.

**Component:** get_communication_stats

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communications' has no rate_limit attribute in the SBOM.

**Component:** get_communications

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_scenarios' has no rate_limit attribute in the SBOM.

**Component:** get_scenarios

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'health_check' has no rate_limit attribute in the SBOM.

**Component:** health_check

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'root' has no rate_limit attribute in the SBOM.

**Component:** root

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'generate_assessment_pdf' has no rate_limit attribute in the SBOM.

**Component:** generate_assessment_pdf

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_agent_status' has no rate_limit attribute in the SBOM.

**Component:** get_agent_status

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communication_stats' has no rate_limit attribute in the SBOM.

**Component:** get_communication_stats

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communications' has no rate_limit attribute in the SBOM.

**Component:** get_communications

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_conversation_history' has no rate_limit attribute in the SBOM.

**Component:** get_conversation_history

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_summary' has no rate_limit attribute in the SBOM.

**Component:** get_patient_summary

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'health_check' has no rate_limit attribute in the SBOM.

**Component:** health_check

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'root' has no rate_limit attribute in the SBOM.

**Component:** root

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_comprehensive_conversation_compat' has no rate_limit attribute in the SBOM.

**Component:** run_comprehensive_conversation_compat

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_emergency_conversation_compat' has no rate_limit attribute in the SBOM.

**Component:** run_emergency_conversation_compat

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_medication_review_compat' has no rate_limit attribute in the SBOM.

**Component:** run_medication_review_compat

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'start_comprehensive_conversation' has no rate_limit attribute in the SBOM.

**Component:** start_comprehensive_conversation

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'start_emergency_conversation' has no rate_limit attribute in the SBOM.

**Component:** start_emergency_conversation

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'start_medication_review_conversation' has no rate_limit attribute in the SBOM.

**Component:** start_medication_review_conversation

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'generate_assessment_pdf' has no rate_limit attribute in the SBOM.

**Component:** generate_assessment_pdf

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_agent_status' has no rate_limit attribute in the SBOM.

**Component:** get_agent_status

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communication_stats' has no rate_limit attribute in the SBOM.

**Component:** get_communication_stats

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communications' has no rate_limit attribute in the SBOM.

**Component:** get_communications

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_summary' has no rate_limit attribute in the SBOM.

**Component:** get_patient_summary

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'health_check' has no rate_limit attribute in the SBOM.

**Component:** health_check

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'root' has no rate_limit attribute in the SBOM.

**Component:** root

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_comprehensive_assessment_compat' has no rate_limit attribute in the SBOM.

**Component:** run_comprehensive_assessment_compat

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_emergency_assessment_compat' has no rate_limit attribute in the SBOM.

**Component:** run_emergency_assessment_compat

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_medication_reconciliation_compat' has no rate_limit attribute in the SBOM.

**Component:** run_medication_reconciliation_compat

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_metadata' has no rate_limit attribute in the SBOM.

**Component:** get_metadata

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_conditions' has no rate_limit attribute in the SBOM.

**Component:** get_patient_conditions

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_medications' has no rate_limit attribute in the SBOM.

**Component:** get_patient_medications

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_observations' has no rate_limit attribute in the SBOM.

**Component:** get_patient_observations

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'health_check' has no rate_limit attribute in the SBOM.

**Component:** health_check

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_conditions' has no rate_limit attribute in the SBOM.

**Component:** search_conditions

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_medication_requests' has no rate_limit attribute in the SBOM.

**Component:** search_medication_requests

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_observations' has no rate_limit attribute in the SBOM.

**Component:** search_observations

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_patients' has no rate_limit attribute in the SBOM.

**Component:** search_patients

## [LOW] Policy Gap: CHECK-004

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'test_fhir_connection' has no rate_limit attribute in the SBOM.

**Component:** test_fhir_connection
