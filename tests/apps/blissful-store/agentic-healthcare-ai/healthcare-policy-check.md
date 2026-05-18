# NuGuard Policy Report

**Generated:** 2026-03-30T21:43:32+00:00  
**LLM:** not used  

**45 gap(s)**

## [MEDIUM] CHECK-001: HITL Enforcement

_Policy defines human-in-the-loop triggers. The SBOM should contain GUARDRAIL nodes (e.g. InputGuardrail, OutputGuardrail) or PROMPT nodes with explicit escalation instructions to enforce them._

**Section:** hitl_triggers

The policy defines HITL triggers but the SBOM contains no GUARDRAIL nodes that could enforce them. Found 11 PROMPT node(s) with related content (prompt-level instructions are weaker than guardrail enforcement).

**Searched:**
- GUARDRAIL nodes: none found in SBOM (122 total nodes)
- PROMPT nodes: found 11 prompt(s) with related content

**Prompt evidence (partial):**
- 'Cardiologist System Message': '…You are a board-certified cardiologist specializing in cardiovascular              disease prevention, diagnosis, and tr…' [matched: intervention, cardiovascular, recommendation]
- 'ClinicalPharmacist System Message': '…You are a clinical pharmacist with expertise in medication therapy              management, drug interactions, and pharm…' [matched: patient, interaction, clinical]
- 'Create Cardiology Agent': '…You are a board-certified cardiologist with expertise in              cardiovascular disease prevention, diagnosis, and…' [matched: cardiovascular, assessment]
- 'Create Pharmacist Agent': '…You are a clinical pharmacist with expertise in              pharmacotherapy, drug interactions, and medication safety.…' [matched: patient, interaction, other]
- 'Cardiologist Backstory': '…You are a board-certified cardiologist with expertise in              cardiovascular disease prevention, diagnosis, and…' [matched: cardiovascular, assessment]
- 'Clinical Pharmacist Backstory': '…You are a clinical pharmacist with expertise in              pharmacotherapy, drug interactions, and medication safety.…' [matched: patient, interaction, other]
- 'Nurse Care Coordinator Backstory': '…You are an experienced registered nurse with expertise in              care coordination, patient education, and care tr…' [matched: patient]
- 'Primary Care Physician Backstory': '…You are an experienced primary care physician with expertise in              internal medicine, preventive care, and car…' [matched: patient, assessment, medicine]
- 'EmergencyPhysician System Message': '…You are an emergency medicine physician with expertise in acute care,              rapid assessment, and emergency inter…' [matched: intervention, emergency, critical]
- 'NurseCoordinator System Message': '…You are an experienced registered nurse specializing in care coordination              and patient education. Your role…' [matched: patient, access]
- 'PrimaryCarePhysician System Message': '…You are an experienced primary care physician with expertise in              comprehensive patient assessment, preventiv…' [matched: patient, medical, assessment]

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Access FHIR patient data (Patient, Condition, MedicationStatement, Observation, Encounter) for any patient other than the one identified in the active session' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Call `/api/export/communications` or any bulk export endpoint without explicit authorization' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Generate or return PDF reports containing PHI unless the requesting client is authenticated and the patient ID matches the session' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Pass raw FHIR responses or internal tool outputs to the user without filtering for prompt-injection content' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action "Use `check_drug_interactions` or `run_clinical_decision_support` results to override a clinician's documented order" does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Invoke AutoGen or CrewAI multi-agent scenarios with `urgency_level: emergency` for non-emergency intents (risk of over-escalation)' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Log or cache LLM communication traces containing PHI beyond the retention window defined in the deployment config' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

## [MEDIUM] CHECK-002: Restricted Action Coverage

_Each policy-restricted action should correspond to a TOOL node in the SBOM so the restriction can be enforced and audited at the tool-call boundary._

**Section:** restricted_actions

Restricted action 'Allow wildcard CORS (`allow_origins=["*"]`) in any environment with access to real patient data' does not match any TOOL node name in the SBOM. Verify the action name is correct.

**Searched:**
- Checked 10 TOOL nodes: cardiovascular_assessment_task, care_coordination_task, clinical_review_task, coordination_task, med_reconciliation_task …

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

Policy defines rate_limits but API_ENDPOINT node 'delete_scenario' has no rate_limit attribute in the SBOM.

**Component:** delete_scenario

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'delete_scenario': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'root' has no rate_limit attribute in the SBOM.

**Component:** root

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'root': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_agent_status' has no rate_limit attribute in the SBOM.

**Component:** get_agent_status

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_agent_status': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communications' has no rate_limit attribute in the SBOM.

**Component:** get_communications

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_communications': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_communication_stats' has no rate_limit attribute in the SBOM.

**Component:** get_communication_stats

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_communication_stats': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_conversation_history' has no rate_limit attribute in the SBOM.

**Component:** get_conversation_history

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_conversation_history': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'export_communications' has no rate_limit attribute in the SBOM.

**Component:** export_communications

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'export_communications': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_conditions' has no rate_limit attribute in the SBOM.

**Component:** search_conditions

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'search_conditions': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_medication_requests' has no rate_limit attribute in the SBOM.

**Component:** search_medication_requests

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'search_medication_requests': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_metadata' has no rate_limit attribute in the SBOM.

**Component:** get_metadata

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_metadata': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_observations' has no rate_limit attribute in the SBOM.

**Component:** search_observations

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'search_observations': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'search_patients' has no rate_limit attribute in the SBOM.

**Component:** search_patients

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'search_patients': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_conditions' has no rate_limit attribute in the SBOM.

**Component:** get_patient_conditions

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_patient_conditions': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_medications' has no rate_limit attribute in the SBOM.

**Component:** get_patient_medications

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_patient_medications': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_patient_observations' has no rate_limit attribute in the SBOM.

**Component:** get_patient_observations

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_patient_observations': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'test_fhir_connection' has no rate_limit attribute in the SBOM.

**Component:** test_fhir_connection

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'test_fhir_connection': not set

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

Policy defines rate_limits but API_ENDPOINT node 'get_patient_summary' has no rate_limit attribute in the SBOM.

**Component:** get_patient_summary

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_patient_summary': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'get_scenarios' has no rate_limit attribute in the SBOM.

**Component:** get_scenarios

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'get_scenarios': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_comprehensive_assessment' has no rate_limit attribute in the SBOM.

**Component:** run_comprehensive_assessment

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'run_comprehensive_assessment': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_emergency_assessment' has no rate_limit attribute in the SBOM.

**Component:** run_emergency_assessment

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'run_emergency_assessment': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_autogen_comprehensive' has no rate_limit attribute in the SBOM.

**Component:** execute_autogen_comprehensive

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'execute_autogen_comprehensive': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_autogen_emergency' has no rate_limit attribute in the SBOM.

**Component:** execute_autogen_emergency

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'execute_autogen_emergency': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_autogen_medication_review' has no rate_limit attribute in the SBOM.

**Component:** execute_autogen_medication_review

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'execute_autogen_medication_review': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_comprehensive_conversation_compat' has no rate_limit attribute in the SBOM.

**Component:** run_comprehensive_conversation_compat

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'run_comprehensive_conversation_compat': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'start_comprehensive_conversation' has no rate_limit attribute in the SBOM.

**Component:** start_comprehensive_conversation

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'start_comprehensive_conversation': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'start_emergency_conversation' has no rate_limit attribute in the SBOM.

**Component:** start_emergency_conversation

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'start_emergency_conversation': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'start_medication_review_conversation' has no rate_limit attribute in the SBOM.

**Component:** start_medication_review_conversation

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'start_medication_review_conversation': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_crewai_comprehensive' has no rate_limit attribute in the SBOM.

**Component:** execute_crewai_comprehensive

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'execute_crewai_comprehensive': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_crewai_emergency' has no rate_limit attribute in the SBOM.

**Component:** execute_crewai_emergency

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'execute_crewai_emergency': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'execute_crewai_medication_review' has no rate_limit attribute in the SBOM.

**Component:** execute_crewai_medication_review

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'execute_crewai_medication_review': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_emergency_conversation_compat' has no rate_limit attribute in the SBOM.

**Component:** run_emergency_conversation_compat

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'run_emergency_conversation_compat': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'generate_assessment_pdf' has no rate_limit attribute in the SBOM.

**Component:** generate_assessment_pdf

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'generate_assessment_pdf': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_medication_reconciliation_compat' has no rate_limit attribute in the SBOM.

**Component:** run_medication_reconciliation_compat

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'run_medication_reconciliation_compat': not set

## [LOW] CHECK-004: Rate Limit Instrumentation

_Policy defines rate limits for API endpoints. API_ENDPOINT nodes in the SBOM should carry a rate_limit attribute so the limits can be verified against the deployed configuration._

**Section:** rate_limits

Policy defines rate_limits but API_ENDPOINT node 'run_medication_review_compat' has no rate_limit attribute in the SBOM.

**Component:** run_medication_review_compat

**Searched:**
- Checked rate_limit attribute on API_ENDPOINT 'run_medication_review_compat': not set
