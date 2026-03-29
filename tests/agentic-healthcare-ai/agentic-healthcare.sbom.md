# SBOM Report: https://github.com/rangoel-nu/agentic-healthcare-ai

**Generated:** 2026-03-29T18:19:03Z  
**Schema version:** 1.4.0  

## Summary

| Field | Value |
| --- | --- |
| AI nodes | 112 |
| Dependencies | 83 |
| Data classification | PHI, PII |
| Classified tables | AssessmentRequest, AssessmentRequest, AssessmentRequest, AssessmentRequest, ClinicalAlert, ClinicalAlert, ClinicalAlert, ClinicalAlert, ClinicalAssessment, ClinicalAssessment, ClinicalAssessment, ClinicalAssessment, ClinicalDecisionSupport, ClinicalDecisionSupport, ClinicalDecisionSupport, ClinicalDecisionSupport, ClinicalTrial, ClinicalTrial, ClinicalTrial, ClinicalTrial, ClinicalWorkflow, ClinicalWorkflow, ClinicalWorkflow, ClinicalWorkflow, ConversationRequest, ConversationRequest, ConversationRequest, ConversationRequest, ConversationResponse, ConversationResponse, ConversationResponse, ConversationResponse, EmergencyConversationRequest, EmergencyConversationRequest, EmergencyConversationRequest, EmergencyConversationRequest, EmergencyRequest, EmergencyRequest, EmergencyRequest, EmergencyRequest, LLMCommunication, LLMCommunication, LLMCommunication, LLMCommunication, Medication, Medication, Medication, Medication, MedicationReconciliationRequest, MedicationReconciliationRequest, MedicationReconciliationRequest, MedicationReconciliationRequest, MedicationReviewRequest, MedicationReviewRequest, MedicationReviewRequest, MedicationReviewRequest, PDFGenerationRequest, PDFGenerationRequest, PDFGenerationRequest, PDFGenerationRequest, PatientDemographics, PatientDemographics, PatientDemographics, PatientDemographics, PatientSummary, PatientSummary, PatientSummary, PatientSummary, QualityMeasure, QualityMeasure, QualityMeasure, QualityMeasure, ScenarioConfig, ScenarioConfig, ScenarioConfig, ScenarioConfig, ScenarioExecutionRequest, ScenarioExecutionRequest, ScenarioExecutionRequest, ScenarioExecutionRequest, ServiceConfig, ServiceConfig, ServiceConfig, ServiceConfig, TaskExecutionRequest, TaskExecutionRequest, TaskExecutionRequest, TaskExecutionRequest, VitalSigns, VitalSigns, VitalSigns, VitalSigns, audit_logs, audit_logs, audit_logs, audit_logs |
| Use case | This application implements an agentic AI workflow with 13 agent(s), 10 tool integration(s), and 0 guardrail control(s). Detected use cases include request triage and routing. Multi-modal support: Voice not supported, Images not supported, Video not supported. |
| Frameworks | autogen, azure_ai_agent_service, langchain, openai_agents, crewai |
| Modalities | TEXT |

## AI Components

| Name | Type | Confidence | Details |
| --- | --- | --- | --- |
| Cardiologist | AGENT | 90% | autogen |
| ClinicalPharmacist | AGENT | 90% | autogen |
| EmergencyPhysician | AGENT | 90% | autogen |
| group_chat | AGENT | 85% | autogen |
| manager | AGENT | 85% | autogen |
| NurseCoordinator | AGENT | 90% | autogen |
| PrimaryCarePhysician | AGENT | 90% | autogen |
| UserProxy | AGENT | 90% | autogen |
| api_router | AGENT | 90% | fastapi |
| app | AGENT | 90% | fastapi |
| app | AGENT | 90% | fastapi |
| app | AGENT | 90% | fastapi |
| app | AGENT | 90% | fastapi |
| generic | API_ENDPOINT | 95% |  |
| delete_scenario | API_ENDPOINT | 90% | DELETE /scenarios/{scenario_id} |
| root | API_ENDPOINT | 90% | GET / |
| get_agent_status | API_ENDPOINT | 90% | GET /agents/status |
| get_communications | API_ENDPOINT | 90% | GET /communications |
| get_communication_stats | API_ENDPOINT | 90% | GET /communications/stats |
| get_conversation_history | API_ENDPOINT | 90% | GET /conversations/history |
| export_communications | API_ENDPOINT | 90% | GET /export/communications |
| search_conditions | API_ENDPOINT | 90% | GET /fhir/Condition |
| search_medication_requests | API_ENDPOINT | 90% | GET /fhir/MedicationRequest |
| get_metadata | API_ENDPOINT | 90% | GET /fhir/metadata |
| search_observations | API_ENDPOINT | 90% | GET /fhir/Observation |
| search_patients | API_ENDPOINT | 90% | GET /fhir/Patient |
| get_patient_conditions | API_ENDPOINT | 90% | GET /fhir/Patient/{patient_id}/Condition |
| get_patient_medications | API_ENDPOINT | 90% | GET /fhir/Patient/{patient_id}/MedicationStatement |
| get_patient_observations | API_ENDPOINT | 90% | GET /fhir/Patient/{patient_id}/Observation |
| test_fhir_connection | API_ENDPOINT | 90% | GET /fhir/test-connection |
| health_check | API_ENDPOINT | 90% | GET /health |
| get_patient_summary | API_ENDPOINT | 90% | GET /patient/{patient_id}/summary |
| get_scenarios | API_ENDPOINT | 90% | GET /scenarios |
| run_comprehensive_assessment | API_ENDPOINT | 90% | POST /assessment/comprehensive |
| run_emergency_assessment | API_ENDPOINT | 90% | POST /assessment/emergency |
| execute_autogen_comprehensive | API_ENDPOINT | 90% | POST /autogen/comprehensive |
| execute_autogen_emergency | API_ENDPOINT | 90% | POST /autogen/emergency |
| execute_autogen_medication_review | API_ENDPOINT | 90% | POST /autogen/medication_review |
| run_comprehensive_conversation_compat | API_ENDPOINT | 90% | POST /comprehensive |
| start_comprehensive_conversation | API_ENDPOINT | 90% | POST /conversation/comprehensive |
| start_emergency_conversation | API_ENDPOINT | 90% | POST /conversation/emergency |
| start_medication_review_conversation | API_ENDPOINT | 90% | POST /conversation/medication-review |
| execute_crewai_comprehensive | API_ENDPOINT | 90% | POST /crewai/comprehensive |
| execute_crewai_emergency | API_ENDPOINT | 90% | POST /crewai/emergency |
| execute_crewai_medication_review | API_ENDPOINT | 90% | POST /crewai/medication_review |
| run_emergency_conversation_compat | API_ENDPOINT | 90% | POST /emergency |
| generate_assessment_pdf | API_ENDPOINT | 90% | POST /generate-pdf |
| run_medication_reconciliation_compat | API_ENDPOINT | 90% | POST /medication-reconciliation |
| run_medication_review_compat | API_ENDPOINT | 90% | POST /medication-review |
| generic | AUTH | 98% |  |
| DefaultAzureCredential | AUTH | 88% |  |
| security | AUTH | 90% | bearer |
| security | AUTH | 90% | bearer |
| nginx:alpine | CONTAINER_IMAGE | 99% | nginx:alpine |
| node:18-alpine | CONTAINER_IMAGE | 99% | node:18-alpine |
| python:3.11-slim | CONTAINER_IMAGE | 99% | python:3.11-slim |
| bigquery | DATASTORE | 70% | PHI, PII |
| elasticsearch | DATASTORE | 95% | PHI, PII |
| postgres | DATASTORE | 98% | PHI, PII |
| redis | DATASTORE | 98% | PHI, PII |
| generic | DEPLOYMENT | 98% |  |
| CI/CD Pipeline | DEPLOYMENT | 95% | github-actions |
| Deploy FHIR Servers Multi-Cloud | DEPLOYMENT | 95% | github-actions |
| Deploy to Amazon Web Services | DEPLOYMENT | 95% | github-actions |
| Deploy to Google Cloud Platform | DEPLOYMENT | 95% | github-actions |
| Deploy to Microsoft Azure | DEPLOYMENT | 95% | github-actions |
| UI Tests | DEPLOYMENT | 95% | github-actions |
| Port 80 | DEPLOYMENT | 90% |  |
| Port 8000 | DEPLOYMENT | 90% |  |
| Port 8001 | DEPLOYMENT | 90% |  |
| Port 8002 | DEPLOYMENT | 90% |  |
| Port 8003 | DEPLOYMENT | 90% |  |
| Port 8004 | DEPLOYMENT | 90% |  |
| autogen | FRAMEWORK | 98% | autogen |
| framework:azure_ai_agent_service | FRAMEWORK | 95% | azure_ai_agent_service |
| framework:langchain | FRAMEWORK | 95% | langchain |
| framework:llm_clients | FRAMEWORK | 95% | llm_clients |
| framework:openai_agents | FRAMEWORK | 95% | openai_agents |
| aws-identity-build-and-push-images | IAM | 93% | role |
| aws-identity-cleanup | IAM | 93% | role |
| aws-identity-create-eks-cluster | IAM | 93% | role |
| aws-identity-create-landing-zone | IAM | 93% | role |
| aws-identity-deploy-aws-fhir | IAM | 93% | role |
| aws-identity-deploy-to-eks | IAM | 93% | role |
| aws-identity-test-deployment | IAM | 93% | role |
| ${{ secrets.AZURE_CREDENTIALS }} | IAM | 93% | managed_identity |
| gpt-3.5-turbo | MODEL | 55% |  |
| gpt-4-turbo | MODEL | 55% |  |
| admin | PRIVILEGE | 60% | admin |
| code_execution | PRIVILEGE | 75% | code_execution |
| db_write | PRIVILEGE | 88% | db_write |
| filesystem_write | PRIVILEGE | 75% | filesystem_write |
| network_out | PRIVILEGE | 83% | network_out |
| rbac | PRIVILEGE | 60% | rbac |
| Cardiologist System Message | PROMPT | 90% | "You are a board-certified cardiologist specializing in cardiovascular           …" · role=system |
| ClinicalPharmacist System Message | PROMPT | 90% | "You are a clinical pharmacist with expertise in medication therapy              …" · role=system |
| Create Cardiology Agent | PROMPT | 60% | "You are a board-certified cardiologist with expertise in              cardiovasc…" · role=system |
| Create Pharmacist Agent | PROMPT | 60% | "You are a clinical pharmacist with expertise in              pharmacotherapy, dr…" · role=system |
| EmergencyPhysician System Message | PROMPT | 90% | "You are an emergency medicine physician with expertise in acute care,           …" · role=system |
| NurseCoordinator System Message | PROMPT | 90% | "You are an experienced registered nurse specializing in care coordination       …" · role=system |
| PrimaryCarePhysician System Message | PROMPT | 90% | "You are an experienced primary care physician with expertise in              com…" · role=system |
| generic | PROMPT | 75% |  |
| cardiovascular_assessment_task | TOOL | 80% | crewai |
| care_coordination_task | TOOL | 80% | crewai |
| clinical_review_task | TOOL | 80% | crewai |
| coordination_task | TOOL | 80% | crewai |
| med_reconciliation_task | TOOL | 80% | crewai |
| medication_review_task | TOOL | 80% | crewai |
| patient_data_task | TOOL | 80% | crewai |
| rapid_medication_check | TOOL | 80% | crewai |
| triage_task | TOOL | 80% | crewai |
| browser_automation | TOOL | 85% | Playwright |

### Prompt Details

**Cardiologist System Message**
- Role: `system`
- Content: You are a board-certified cardiologist specializing in cardiovascular              disease prevention, diagnosis, and treatment. Your expertise includes:             1. Cardiovascular risk stratification             2. Heart disease diagnosis and management             3. Hypertension management    …

**ClinicalPharmacist System Message**
- Role: `system`
- Content: You are a clinical pharmacist with expertise in medication therapy              management, drug interactions, and pharmaceutical care. Your responsibilities include:             1. Medication reconciliation and review             2. Drug interaction screening             3. Dosing optimization     …

**Create Cardiology Agent**
- Role: `system`
- Content: You are a board-certified cardiologist with expertise in              cardiovascular disease prevention, diagnosis, and treatment. You specialize              in risk assessment, ECG interpretation, and evidence-based cardiovascular              therapeutics.

**Create Pharmacist Agent**
- Role: `system`
- Content: You are a clinical pharmacist with expertise in              pharmacotherapy, drug interactions, and medication safety. You focus on              medication reconciliation, dosing optimization, and patient education              about medications.

**EmergencyPhysician System Message**
- Role: `system`
- Content: You are an emergency medicine physician with expertise in acute care,              rapid assessment, and emergency interventions. Your focus areas include:             1. Rapid triage and assessment             2. Emergency stabilization             3. Critical decision making under time pressure   …

**NurseCoordinator System Message**
- Role: `system`
- Content: You are an experienced registered nurse specializing in care coordination              and patient education. Your role encompasses:             1. Care transition management             2. Patient and family education             3. Discharge planning             4. Follow-up coordination          …

**PrimaryCarePhysician System Message**
- Role: `system`
- Content: You are an experienced primary care physician with expertise in              comprehensive patient assessment, preventive care, and care coordination. Your role is to:             1. Conduct thorough patient evaluations             2. Identify and prioritize health issues             3. Coordinate c…

**generic**

### Datastore Details

**bigquery**
- Classification: **PHI, PII**
- Classified tables: `AssessmentRequest`, `ClinicalAlert`, `ClinicalAssessment`, `ClinicalDecisionSupport`, `ClinicalTrial`, `ClinicalWorkflow`, `ConversationRequest`, `ConversationResponse`, `EmergencyConversationRequest`, `EmergencyRequest`, `LLMCommunication`, `Medication`, `MedicationReconciliationRequest`, `MedicationReviewRequest`, `PDFGenerationRequest`, `PatientDemographics`, `PatientSummary`, `QualityMeasure`, `ScenarioConfig`, `ScenarioExecutionRequest`, `ServiceConfig`, `TaskExecutionRequest`, `VitalSigns`, `audit_logs`
- Sensitive fields:
  - `ScenarioConfig`: `chief_complaint`, `patient_id`
  - `TaskExecutionRequest`: `patient_id`
  - `ScenarioExecutionRequest`: `patient_id`
  - `ConversationRequest`: `chief_complaint`, `patient_id`
  - `EmergencyConversationRequest`: `chief_complaint`, `patient_id`
  - `MedicationReviewRequest`: `patient_id`
  - `ConversationResponse`: `patient_id`
  - `PDFGenerationRequest`: `patient_id`
  - `ServiceConfig`: `name`
  - `AssessmentRequest`: `chief_complaint`, `patient_id`
  - `EmergencyRequest`: `chief_complaint`, `patient_id`
  - `MedicationReconciliationRequest`: `patient_id`
  - `audit_logs`: `ip_address`
  - `PatientDemographics`: `address`, `birth_date`, `email`, `gender`, `name`, `patient_id`, `phone`
  - `VitalSigns`: `heart_rate`
  - `Medication`: `name`
  - `ClinicalAssessment`: `chief_complaint`, `patient_id`
  - `ClinicalAlert`: `patient_id`
  - `PatientSummary`: `allergies`
  - `ClinicalDecisionSupport`: `patient_id`
  - `ClinicalWorkflow`: `name`
  - `QualityMeasure`: `name`
  - `ClinicalTrial`: `condition`
  - `LLMCommunication`: `patient_id`

**elasticsearch**
- Classification: **PHI, PII**
- Classified tables: `AssessmentRequest`, `ClinicalAlert`, `ClinicalAssessment`, `ClinicalDecisionSupport`, `ClinicalTrial`, `ClinicalWorkflow`, `ConversationRequest`, `ConversationResponse`, `EmergencyConversationRequest`, `EmergencyRequest`, `LLMCommunication`, `Medication`, `MedicationReconciliationRequest`, `MedicationReviewRequest`, `PDFGenerationRequest`, `PatientDemographics`, `PatientSummary`, `QualityMeasure`, `ScenarioConfig`, `ScenarioExecutionRequest`, `ServiceConfig`, `TaskExecutionRequest`, `VitalSigns`, `audit_logs`
- Sensitive fields:
  - `ScenarioConfig`: `chief_complaint`, `patient_id`
  - `TaskExecutionRequest`: `patient_id`
  - `ScenarioExecutionRequest`: `patient_id`
  - `ConversationRequest`: `chief_complaint`, `patient_id`
  - `EmergencyConversationRequest`: `chief_complaint`, `patient_id`
  - `MedicationReviewRequest`: `patient_id`
  - `ConversationResponse`: `patient_id`
  - `PDFGenerationRequest`: `patient_id`
  - `ServiceConfig`: `name`
  - `AssessmentRequest`: `chief_complaint`, `patient_id`
  - `EmergencyRequest`: `chief_complaint`, `patient_id`
  - `MedicationReconciliationRequest`: `patient_id`
  - `audit_logs`: `ip_address`
  - `PatientDemographics`: `address`, `birth_date`, `email`, `gender`, `name`, `patient_id`, `phone`
  - `VitalSigns`: `heart_rate`
  - `Medication`: `name`
  - `ClinicalAssessment`: `chief_complaint`, `patient_id`
  - `ClinicalAlert`: `patient_id`
  - `PatientSummary`: `allergies`
  - `ClinicalDecisionSupport`: `patient_id`
  - `ClinicalWorkflow`: `name`
  - `QualityMeasure`: `name`
  - `ClinicalTrial`: `condition`
  - `LLMCommunication`: `patient_id`

**postgres**
- Classification: **PHI, PII**
- Classified tables: `AssessmentRequest`, `ClinicalAlert`, `ClinicalAssessment`, `ClinicalDecisionSupport`, `ClinicalTrial`, `ClinicalWorkflow`, `ConversationRequest`, `ConversationResponse`, `EmergencyConversationRequest`, `EmergencyRequest`, `LLMCommunication`, `Medication`, `MedicationReconciliationRequest`, `MedicationReviewRequest`, `PDFGenerationRequest`, `PatientDemographics`, `PatientSummary`, `QualityMeasure`, `ScenarioConfig`, `ScenarioExecutionRequest`, `ServiceConfig`, `TaskExecutionRequest`, `VitalSigns`, `audit_logs`
- Sensitive fields:
  - `ScenarioConfig`: `chief_complaint`, `patient_id`
  - `TaskExecutionRequest`: `patient_id`
  - `ScenarioExecutionRequest`: `patient_id`
  - `ConversationRequest`: `chief_complaint`, `patient_id`
  - `EmergencyConversationRequest`: `chief_complaint`, `patient_id`
  - `MedicationReviewRequest`: `patient_id`
  - `ConversationResponse`: `patient_id`
  - `PDFGenerationRequest`: `patient_id`
  - `ServiceConfig`: `name`
  - `AssessmentRequest`: `chief_complaint`, `patient_id`
  - `EmergencyRequest`: `chief_complaint`, `patient_id`
  - `MedicationReconciliationRequest`: `patient_id`
  - `audit_logs`: `ip_address`
  - `PatientDemographics`: `address`, `birth_date`, `email`, `gender`, `name`, `patient_id`, `phone`
  - `VitalSigns`: `heart_rate`
  - `Medication`: `name`
  - `ClinicalAssessment`: `chief_complaint`, `patient_id`
  - `ClinicalAlert`: `patient_id`
  - `PatientSummary`: `allergies`
  - `ClinicalDecisionSupport`: `patient_id`
  - `ClinicalWorkflow`: `name`
  - `QualityMeasure`: `name`
  - `ClinicalTrial`: `condition`
  - `LLMCommunication`: `patient_id`

**redis**
- Classification: **PHI, PII**
- Classified tables: `AssessmentRequest`, `ClinicalAlert`, `ClinicalAssessment`, `ClinicalDecisionSupport`, `ClinicalTrial`, `ClinicalWorkflow`, `ConversationRequest`, `ConversationResponse`, `EmergencyConversationRequest`, `EmergencyRequest`, `LLMCommunication`, `Medication`, `MedicationReconciliationRequest`, `MedicationReviewRequest`, `PDFGenerationRequest`, `PatientDemographics`, `PatientSummary`, `QualityMeasure`, `ScenarioConfig`, `ScenarioExecutionRequest`, `ServiceConfig`, `TaskExecutionRequest`, `VitalSigns`, `audit_logs`
- Sensitive fields:
  - `ScenarioConfig`: `chief_complaint`, `patient_id`
  - `TaskExecutionRequest`: `patient_id`
  - `ScenarioExecutionRequest`: `patient_id`
  - `ConversationRequest`: `chief_complaint`, `patient_id`
  - `EmergencyConversationRequest`: `chief_complaint`, `patient_id`
  - `MedicationReviewRequest`: `patient_id`
  - `ConversationResponse`: `patient_id`
  - `PDFGenerationRequest`: `patient_id`
  - `ServiceConfig`: `name`
  - `AssessmentRequest`: `chief_complaint`, `patient_id`
  - `EmergencyRequest`: `chief_complaint`, `patient_id`
  - `MedicationReconciliationRequest`: `patient_id`
  - `audit_logs`: `ip_address`
  - `PatientDemographics`: `address`, `birth_date`, `email`, `gender`, `name`, `patient_id`, `phone`
  - `VitalSigns`: `heart_rate`
  - `Medication`: `name`
  - `ClinicalAssessment`: `chief_complaint`, `patient_id`
  - `ClinicalAlert`: `patient_id`
  - `PatientSummary`: `allergies`
  - `ClinicalDecisionSupport`: `patient_id`
  - `ClinicalWorkflow`: `name`
  - `QualityMeasure`: `name`
  - `ClinicalTrial`: `condition`
  - `LLMCommunication`: `patient_id`

### Model Details

| Model | Provider | Family | API Endpoint |
| --- | --- | --- | --- |
| gpt-3.5-turbo |  |  |  |
| gpt-4-turbo |  |  |  |

### Tool Details

**cardiovascular_assessment_task (`crewai_task_cardiovascular_assessment_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 355 — crewai: Task(description=...)

**care_coordination_task (`crewai_task_care_coordination_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 371 — crewai: Task(description=...)

**clinical_review_task (`crewai_task_clinical_review_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 433 — crewai: Task(description=...)

**coordination_task (`crewai_task_coordination_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 441 — crewai: Task(description=...)

**med_reconciliation_task (`crewai_task_med_reconciliation_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 425 — crewai: Task(description=...)

**medication_review_task (`crewai_task_medication_review_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 363 — crewai: Task(description=...)

**patient_data_task (`crewai_task_patient_data_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 347 — crewai: Task(description=...)

**rapid_medication_check (`crewai_task_rapid_medication_check`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 407 — crewai: Task(description=...)

**triage_task (`crewai_task_triage_task`)**
- Adapter: `crewai`
- Detected at: `crewai_fhir_agent/agents.py` line 399 — crewai: Task(description=...)

**browser_automation (`tool_browser_automation`)**
- Adapter: `tool_browser_automation`
- Detected at: `.github/workflows/ui-tests.yml` line 128 — tool_browser_automation: Playwright

### Deployment Details

**generic (`deployment_generic`)**
- `Docker` — `.github/workflows/ci-cd.yml`:12, `docker/docker-compose.config.yml`:1, `docker/scripts/cleanup.sh`:3, `docker/scripts/deploy.sh`:3, `fhir_mcp_server/Dockerfile`:17
- `aws cloudformation` — `.github/workflows/deploy-aws.yml`:530
- `az group` — `.github/workflows/deploy-azure.yml`:112
- `gcloud` — `.github/workflows/deploy-fhir-servers.yml`:144, `.github/workflows/deploy-gcp.yml`:98, `.github/workflows/env-template.yml`:101
- `traefik` — `docker/docker-compose.agents.yml`:36
- `docker` — `docker/docker-compose.yml`:22, `scripts/setup-config.py`:51, `setup.py`:41, `.github/workflows/ui-tests.yml`:147
- `kubectl` — `kubernetes/cleanup.sh`:10, `kubernetes/deploy.sh`:42, `kubernetes/port-forward.sh`:53, `scripts/clean-up-docker.sh`:20, `kubernetes/manifests/02-secrets.yaml`:31
- `nginx` — `ui/Dockerfile`:36, `ui/docker-compose.ui.yml`:13
- `Deployment` — `kubernetes/manifests/06-healthcare-agents.yaml`:2, `kubernetes/manifests/07-healthcare-ui.yaml`:2, `kubernetes/manifests/09-elk-stack.yaml`:2, `kubernetes/manifests/04-database.yaml`:2, `kubernetes/manifests/08-monitoring.yaml`:2, `kubernetes/manifests/11-fhir-proxy.yaml`:2, `kubernetes/manifests/05-redis.yaml`:2
- `KUBERNETES` — `config/secrets.py`:25
- `Kubernetes` — `kubernetes/manifests/configmap-generator.yaml`:1, `scripts/generate-k8s-configmap.py`:3
- `NGINX` — `docker/services/monitoring/prometheus.yml`:40
- `uvicorn` — `agent_backend/main.py`:458, `autogen_fhir_agent/main.py`:11, `crewai_fhir_agent/main.py`:11, `fhir_mcp_server/fhir_mcp_service.py`:22, `fhir_proxy/main.py`:241, `agent_backend/Dockerfile`:34, `fhir_proxy/Dockerfile`:25
- `render` — `ui/src/index.tsx`:49
- Source tiers: code, iac

**CI/CD Pipeline (`deployment_github_actions_ci_cd_pipeline`)**
- `GitHub Actions: CI/CD Pipeline triggers=['workflow_dispatch']` — `.github/workflows/ci-cd.yml`:1

**Deploy FHIR Servers Multi-Cloud (`deployment_github_actions_deploy_fhir_servers_multi_cloud`)**
- `GitHub Actions: Deploy FHIR Servers Multi-Cloud triggers=['workflow_dispatch']` — `.github/workflows/deploy-fhir-servers.yml`:1

**Deploy to Amazon Web Services (`deployment_github_actions_deploy_to_amazon_web_services`)**
- `GitHub Actions: Deploy to Amazon Web Services triggers=['workflow_dispatch']` — `.github/workflows/deploy-aws.yml`:1

**Deploy to Google Cloud Platform (`deployment_github_actions_deploy_to_google_cloud_platform`)**
- `GitHub Actions: Deploy to Google Cloud Platform triggers=['workflow_dispatch']` — `.github/workflows/deploy-gcp.yml`:1

**Deploy to Microsoft Azure (`deployment_github_actions_deploy_to_microsoft_azure`)**
- `GitHub Actions: Deploy to Microsoft Azure triggers=['workflow_dispatch']` — `.github/workflows/deploy-azure.yml`:1

**UI Tests (`deployment_github_actions_ui_tests`)**
- `GitHub Actions: UI Tests triggers=['workflow_dispatch']` — `.github/workflows/ui-tests.yml`:1

**Port 80 (`deployment_port_80`)**
- `EXPOSE 80` — `ui/Dockerfile`:50

**Port 8000 (`deployment_port_8000`)**
- `EXPOSE 8000` — `crewai_fhir_agent/Dockerfile`:35

**Port 8001 (`deployment_port_8001`)**
- `EXPOSE 8001` — `autogen_fhir_agent/Dockerfile`:35

**Port 8002 (`deployment_port_8002`)**
- `EXPOSE 8002` — `agent_backend/Dockerfile`:27

**Port 8003 (`deployment_port_8003`)**
- `EXPOSE 8003` — `fhir_proxy/Dockerfile`:18

**Port 8004 (`deployment_port_8004`)**
- `EXPOSE 8004` — `fhir_mcp_server/Dockerfile`:32

### Container Images

| Image | Registry | Multi-stage | Security Findings |
| --- | --- | --- | --- |
| nginx:alpine | docker.io | Yes | secrets_in_build_args |
| node:18-alpine | docker.io | Yes | secrets_in_build_args |
| python:3.11-slim | docker.io | No | — |

### Privileges

- **admin**: scope=`admin`
- **code_execution**: scope=`code_execution`
- **db_write**: scope=`db_write`
- **filesystem_write**: scope=`filesystem_write`
- **network_out**: scope=`network_out`
- **rbac**: scope=`rbac`

### IAM Entities

**aws-identity-build-and-push-images**
- Type: `role`
- Principal: `aws-identity-build-and-push-images`

**aws-identity-cleanup**
- Type: `role`
- Principal: `aws-identity-cleanup`

**aws-identity-create-eks-cluster**
- Type: `role`
- Principal: `aws-identity-create-eks-cluster`

**aws-identity-create-landing-zone**
- Type: `role`
- Principal: `aws-identity-create-landing-zone`

**aws-identity-deploy-aws-fhir**
- Type: `role`
- Principal: `aws-identity-deploy-aws-fhir`

**aws-identity-deploy-to-eks**
- Type: `role`
- Principal: `aws-identity-deploy-to-eks`

**aws-identity-test-deployment**
- Type: `role`
- Principal: `aws-identity-test-deployment`

**${{ secrets.AZURE_CREDENTIALS }}**
- Type: `managed_identity`
- Principal: `${{ secrets.AZURE_CREDENTIALS }}`

## Dependencies

| Name | Version | Group | License |
| --- | --- | --- | --- |
| fastapi | ==0.104.1 | runtime |  |
| uvicorn | [standard]==0.24.0 | runtime |  |
| pydantic | >=2.7.0,<3.0.0 | runtime |  |
| requests | ==2.32.4 | runtime |  |
| python-multipart | ==0.0.22 | runtime |  |
| openai | >=1.13.3,<2.0.0 | runtime |  |
| fhirclient | ==4.1.0 | runtime |  |
| python-dateutil | ==2.8.2 | runtime |  |
| pandas | ==2.1.3 | runtime |  |
| numpy | ==1.25.2 | runtime |  |
| python-dotenv | ==1.0.0 | runtime |  |
| aiofiles | ==23.2.1 | runtime |  |
| pytest | ==8.2.0 | runtime |  |
| pytest-asyncio | ==0.21.1 | runtime |  |
| httpx | ==0.25.2 | runtime |  |
| pyautogen | ==0.2.16 | runtime |  |
| crewai | ==0.51.0 | runtime |  |
| reportlab | ==4.0.7 | runtime |  |
| fhir-resources | ==7.1.0 | runtime |  |
| python-jose | [cryptography]==3.4.0 | runtime |  |
| passlib | [bcrypt]==1.7.4 | runtime |  |
| cryptography | ==46.0.5 | runtime |  |
| pyjwt | ==2.8.0 | runtime |  |
| scikit-learn | ==1.5.0 | runtime |  |
| matplotlib | ==3.8.2 | runtime |  |
| seaborn | ==0.13.0 | runtime |  |
| streamlit | ==1.37.0 | runtime |  |
| docker | ==6.1.3 | runtime |  |
| aiohttp | ==3.13.3 | runtime |  |
| pathlib |  | runtime |  |
| langchain-openai | ==0.1.8 | runtime |  |
| aiohappyeyeballs | ==2.6.1 | runtime |  |
| aiosignal | ==1.4.0 | runtime |  |
| annotated-types | ==0.7.0 | runtime |  |
| anyio | ==4.9.0 | runtime |  |
| attrs | ==25.3.0 | runtime |  |
| certifi | ==2025.4.26 | runtime |  |
| charset-normalizer | ==3.4.2 | runtime |  |
| click | ==8.2.1 | runtime |  |
| fhirpy | ==2.0.15 | runtime |  |
| frozenlist | ==1.6.0 | runtime |  |
| h11 | ==0.16.0 | runtime |  |
| httpcore | ==1.0.9 | runtime |  |
| httpx-sse | ==0.4.0 | runtime |  |
| idna | ==3.10 | runtime |  |
| markdown-it-py | ==3.0.0 | runtime |  |
| mcp | ==1.23.0 | runtime |  |
| mdurl | ==0.1.2 | runtime |  |
| multidict | ==6.4.4 | runtime |  |
| propcache | ==0.3.1 | runtime |  |
| pydantic-core | ==2.33.2 | runtime |  |
| pydantic-settings | ==2.9.1 | runtime |  |
| pygments | ==2.19.1 | runtime |  |
| pytz | ==2025.2 | runtime |  |
| rich | ==14.0.0 | runtime |  |
| shellingham | ==1.5.4 | runtime |  |
| sniffio | ==1.3.1 | runtime |  |
| sse-starlette | ==2.3.5 | runtime |  |
| starlette | ==0.49.1 | runtime |  |
| typer | ==0.16.0 | runtime |  |
| typing-extensions | ==4.13.2 | runtime |  |
| typing-inspection | ==0.4.1 | runtime |  |
| urllib3 | ==2.6.3 | runtime |  |
| yarl | ==1.20.0 | runtime |  |
| @emotion/react | ^11.11.1 | runtime |  |
| @emotion/styled | ^11.11.0 | runtime |  |
| @mui/icons-material | ^5.14.19 | runtime |  |
| @mui/material | ^5.14.20 | runtime |  |
| @mui/x-date-pickers | ^6.18.2 | runtime |  |
| @reduxjs/toolkit | ^2.0.1 | runtime |  |
| axios | ^1.6.2 | runtime |  |
| http-proxy-middleware | ^3.0.0 | runtime |  |
| react | ^18.2.0 | runtime |  |
| react-dom | ^18.2.0 | runtime |  |
| react-redux | ^9.0.4 | runtime |  |
| react-router-dom | ^6.20.1 | runtime |  |
| react-scripts | 5.0.1 | runtime |  |
| recharts | ^2.8.0 | runtime |  |
| socket.io-client | ^4.7.4 | runtime |  |
| typescript | ^4.9.5 | runtime |  |
| web-vitals | ^3.5.0 | runtime |  |
| @types/react | ^18.2.42 | dev |  |
| @types/react-dom | ^18.2.17 | dev |  |

## Node Type Breakdown

| Type | Count |
| --- | --- |
| AGENT | 13 |
| API_ENDPOINT | 36 |
| AUTH | 4 |
| CONTAINER_IMAGE | 3 |
| DATASTORE | 4 |
| DEPLOYMENT | 13 |
| FRAMEWORK | 5 |
| IAM | 8 |
| MODEL | 2 |
| PRIVILEGE | 6 |
| PROMPT | 8 |
| TOOL | 10 |
