# SBOM Report: https://github.com/NuGuardAI/Healthcare-voice-agent

**Generated:** 2026-03-29T01:05:00Z  
**Schema version:** 1.4.0  

## Summary

| Field | Value |
| --- | --- |
| AI nodes | 30 |
| Dependencies | 36 |
| Data classification | PHI, PII |
| Classified tables | AppointmentRequest, LoginRequest, MedicalHistoryResponse, PatientDetailsResponse, appointments, doctors, hospitals, patient_history, patients, specialists, symptoms, users |
| Use case | This application implements an agentic AI workflow with 6 agent(s), 1 tool integration(s), and 0 guardrail control(s). Detected use cases include specialist recommendation workflows, search-based retrieval. Multi-modal support: Voice supported, Images not supported, Video not supported. |
| Frameworks | langgraph, langchain |
| Modalities | TEXT, VOICE |

## AI Components

| Name | Type | Confidence | Details |
| --- | --- | --- | --- |
| app | AGENT | 90% | fastapi |
| fetch_doctor_details_agent | AGENT | 85% | langgraph |
| normalize_agent | AGENT | 85% | langgraph |
| prognosis_search_agent | AGENT | 85% | langgraph |
| recommend_specialists_agent | AGENT | 85% | langgraph |
| specialist_lookup_agent | AGENT | 85% | langgraph |
| generic | API_ENDPOINT | 90% |  |
| Port 8080 | API_ENDPOINT | 90% | 8080 |
| create_appointment | API_ENDPOINT | 90% | POST /appointments |
| get_medical_history | API_ENDPOINT | 90% | GET /medical-history/{user_id} |
| get_patient_details | API_ENDPOINT | 90% | GET /patient-details/{user_id} |
| health_check | API_ENDPOINT | 90% | GET /api/health |
| login | API_ENDPOINT | 90% | POST /login |
| normalize | API_ENDPOINT | 90% | POST /normalize |
| run_langgraph | API_ENDPOINT | 90% | POST /run_langgraph |
| serve_spa | API_ENDPOINT | 90% | GET /{full_path:path} |
| generic | AUTH | 60% |  |
| node:20 | CONTAINER_IMAGE | 99% | node:20 |
| python:3.11-slim | CONTAINER_IMAGE | 99% | python:3.11-slim |
| postgres | DATASTORE | 95% | PHI, PII |
| generic | DEPLOYMENT | 98% |  |
| framework:langgraph | FRAMEWORK | 95% | langgraph |
| llm_clients_ts | FRAMEWORK | 85% | google |
| gemini-2.0-flash | MODEL | 88% | google |
| gpt-4 | MODEL | 90% | openai |
| admin | PRIVILEGE | 60% | admin |
| db_write | PRIVILEGE | 95% | db_write |
| System Instruction | PROMPT | 65% | "You are a helpful AI Medical Triage Assistant. Your goal is to gather a clear li…" · role=system |
| System Message | PROMPT | 80% | "You are an intelligent medical assistant that triages patients." · role=system |
| search | TOOL | 75% | duckduckgo_search |

### Prompt Details

**System Instruction**
- Role: `system`
- Content: You are a helpful AI Medical Triage Assistant. Your goal is to gather a clear list of symptoms from the patient 

**System Message**
- Role: `system`
- Content: You are an intelligent medical assistant that triages patients.

### Datastore Details

**postgres**
- Classification: **PHI, PII**
- Classified tables: `AppointmentRequest`, `LoginRequest`, `MedicalHistoryResponse`, `PatientDetailsResponse`, `appointments`, `doctors`, `hospitals`, `patient_history`, `patients`, `specialists`, `symptoms`, `users`
- Sensitive fields:
  - `LoginRequest`: `email`, `password`
  - `AppointmentRequest`: `patient_id`
  - `PatientDetailsResponse`: `blood_group`, `contact_number`, `date_of_birth`, `gender`, `marital_status`, `medical_record_number`, `name`
  - `MedicalHistoryResponse`: `family_medical_history`, `hospital_admissions`, `immunization_records`, `past_diagnoses`, `surgeries`
  - `users`: `email`, `password`
  - `patients`: `blood_group`, `contact_number`, `date_of_birth`, `gender`, `marital_status`, `medical_record_number`, `name`
  - `patient_history`: `family_medical_history`, `hospital_admissions`, `immunization_records`, `past_diagnoses`, `patient_id`, `surgeries`
  - `hospitals`: `address`, `contact_number`, `name`
  - `specialists`: `name`
  - `doctors`: `name`
  - `appointments`: `patient_id`
  - `symptoms`: `name`

### Model Details

| Model | Provider | Family | API Endpoint |
| --- | --- | --- | --- |
| gemini-2.0-flash | google |  | https://generativelanguage.googleapis.com |
| gpt-4 | openai | gpt | https://api.openai.com/v1 |

### Tool Details

**search (`tool_search`)**
- Adapter: `tool_search`
- Detected at: `backend/langgraph_llm_agents.py` line 7 — tool_search: duckduckgo_search

### Deployment Details

**generic (`deployment_generic`)**
- `az login` — `deploy_azure.sh`:23
- `Docker` — `host_local.sh`:8
- `docker` — `init-db.sh`:7, `sql/init-db.sh`:7, `docker-compose.yml`:11
- `gcloud` — `deploy.sh`:4
- `uvicorn` — `Dockerfile`:41
- `render` — `src/main.jsx`:7
- Source tiers: code, iac

### Container Images

| Image | Registry | Multi-stage | Security Findings |
| --- | --- | --- | --- |
| node:20 | docker.io | Yes | secrets_in_build_args |
| python:3.11-slim | docker.io | Yes | secrets_in_build_args |

### Privileges

- **admin**: scope=`admin`
- **db_write**: scope=`db_write`

## Dependencies

| Name | Version | Group | License |
| --- | --- | --- | --- |
| fastapi |  | runtime |  |
| uvicorn |  | runtime |  |
| psycopg2-binary |  | runtime |  |
| langgraph |  | runtime |  |
| langchain |  | runtime |  |
| openai |  | runtime |  |
| google-generativeai |  | runtime |  |
| pydantic |  | runtime |  |
| python-dotenv |  | runtime |  |
| requests |  | runtime |  |
| tqdm |  | runtime |  |
| numpy |  | runtime |  |
| langchain-openai |  | runtime |  |
| duckduckgo-search |  | runtime |  |
| langchain-community |  | runtime |  |
| @google/genai | ^0.10.0 | runtime |  |
| @google/generative-ai | ^0.24.0 | runtime |  |
| assemblyai | ^4.12.2 | runtime |  |
| openai | ^4.96.0 | runtime |  |
| react | ^19.0.0 | runtime |  |
| react-dom | ^19.0.0 | runtime |  |
| react-router-dom | ^7.5.0 | runtime |  |
| recordrtc | ^5.5.1 | runtime |  |
| @eslint/js | ^9.21.0 | dev |  |
| @playwright/test | ^1.57.0 | dev |  |
| @types/react | ^19.0.10 | dev |  |
| @types/react-dom | ^19.0.4 | dev |  |
| @vitejs/plugin-react | ^4.3.4 | dev |  |
| eslint | ^9.21.0 | dev |  |
| eslint-plugin-react-hooks | ^5.1.0 | dev |  |
| eslint-plugin-react-refresh | ^0.4.19 | dev |  |
| globals | ^15.15.0 | dev |  |
| vite | ^6.2.0 | dev |  |
| cors | ^2.8.5 | runtime |  |
| dotenv | ^16.5.0 | runtime |  |
| express | ^5.1.0 | runtime |  |

## Node Type Breakdown

| Type | Count |
| --- | --- |
| AGENT | 6 |
| API_ENDPOINT | 10 |
| AUTH | 1 |
| CONTAINER_IMAGE | 2 |
| DATASTORE | 1 |
| DEPLOYMENT | 1 |
| FRAMEWORK | 2 |
| MODEL | 2 |
| PRIVILEGE | 2 |
| PROMPT | 2 |
| TOOL | 1 |
