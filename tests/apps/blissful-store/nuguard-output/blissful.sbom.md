# SBOM Report: /workspaces/nuguard-private/tests/blissful-store

**Generated:** 2026-04-01T22:06:16Z  
**Schema version:** 1.4.0  

## Summary

| Field | Value |
| --- | --- |
| AI nodes | 30 |
| Dependencies | 1 |
| Use case | This application implements an agentic AI workflow with 3 agent(s), 13 tool integration(s), and 2 guardrail control(s). Detected use cases include general agentic task orchestration. Multi-modal support: Voice not supported, Images not supported, Video not supported. |
| Frameworks | google_adk |
| Modalities | TEXT |

## AI Components

| Name | Type | Confidence | Details |
| --- | --- | --- | --- |
| cymbal_upsell_agent | AGENT | 90% | google_adk |
| out_of_scope_handling | AGENT | 90% | google_adk |
| retail_agent | AGENT | 90% | google_adk |
| generic | AUTH | 70% |  |
| generic | DEPLOYMENT | 78% |  |
| bad words | GUARDRAIL | 90% | google_adk |
| Prompt Guardrail 1757021081696 | GUARDRAIL | 90% | google_adk |
| gemini-2.5-flash-001 | MODEL | 90% |  |
| gemini-3.0-flash-001 | MODEL | 90% |  |
| code_execution | PRIVILEGE | 60% | code_execution |
| filesystem_write | PRIVILEGE | 55% | filesystem_write |
| network_out | PRIVILEGE | 55% | network_out |
| Blissful Home store instruction | PROMPT | 90% | "CURRENT CUSTOMER PROFILE: {customer_profile}  <persona>     You are an AI assist…" · role=system |
| cymbal_upsell_agent instruction | PROMPT | 90% | "<role>     You are a service specialist for Blissful Home & Garden. Your specifi…" · role=system |
| out_of_scope_handling instruction | PROMPT | 90% | "<role>You are a fallback agent activated when a user's query is off-topic for th…" · role=system |
| generic | PROMPT | 60% |  |
| retail_agent instruction | PROMPT | 90% | "<role>     You are "Project Pro," a primary AI assistant for Blissful Home & Gar…" · role=system |
| affirmative | TOOL | 90% | google_adk |
| apply_discount_to_service | TOOL | 90% | google_adk |
| approve_discount | TOOL | 90% | google_adk |
| ask_to_modify_cart | TOOL | 90% | google_adk |
| get_available_planting_times | TOOL | 90% | google_adk |
| get_landscaping_quote | TOOL | 90% | google_adk |
| get_product_recommendations | TOOL | 90% | google_adk |
| greeting | TOOL | 90% | google_adk |
| request_image_upload | TOOL | 90% | google_adk |
| schedule_planting_service | TOOL | 90% | google_adk |
| crm_service | TOOL | 85% | google_adk |
| update_cart | TOOL | 90% | google_adk |
| update_salesforce_crm | TOOL | 90% | google_adk |

### Prompt Details

**Blissful Home store instruction**
- Role: `system`
- Content: CURRENT CUSTOMER PROFILE: {customer_profile}  <persona>     You are an AI assistant for Blissful Home & Garden, a retailer specializing in home improvement and gardening. Your goal is to provide excellent, helpful, and friendly customer service.      You have access to internal tools that are your p…
- Template variables: `{customer_profile}`

**cymbal_upsell_agent instruction**
- Role: `system`
- Content: <role>     You are a service specialist for Blissful Home & Garden. Your specific role is to handle service inquiries, manage discounts, and schedule appointments after a customer has finished their product selection with a retail agent. You will have access to the full conversation history and must…

**out_of_scope_handling instruction**
- Role: `system`
- Content: <role>You are a fallback agent activated when a user's query is off-topic for the main demo experience. Your purpose is to "break the fourth wall," explain the key features of the agent building platform, and then guide the user back to the main agent.</role> <persona>     Your primary goal is to ac…

**generic**

**retail_agent instruction**
- Role: `system`
- Content: <role>     You are "Project Pro," a primary AI assistant for Blissful Home & Garden store. Your role is to assist with product selection, identification, and shopping cart management. </role> <persona>     Maintain a friendly, empathetic, and helpful tone, but keep your conversational turns concise …

### Model Details

| Model | Provider | Family | API Endpoint |
| --- | --- | --- | --- |
| gemini-2.5-flash-001 |  |  |  |
| gemini-3.0-flash-001 |  |  |  |

### Tool Details

**affirmative (`affirmative`)**
- Adapter: `google_adk_json`
- Detected at: `tools/affirmative/affirmative.json` line 8 — google_adk_json: tool: affirmative — Indicates a verbal affirmative from the user was provided to

**apply_discount_to_service (`apply_discount_to_service`)**
- Adapter: `google_adk_json`
- Detected at: `tools/apply_discount_to_service/apply_discount_to_service.json` line 8 — google_adk_json: tool: apply_discount_to_service — Applies the discount to the specified services.

**approve_discount (`approve_discount`)**
- Adapter: `google_adk_json`
- Detected at: `tools/approve_discount/approve_discount.json` line 8 — google_adk_json: tool: approve_discount — Approve the flat rate or percentage discount requested by th

**ask_to_modify_cart (`ask_to_modify_cart`)**
- Adapter: `google_adk_json`
- Detected at: `tools/ask_to_modify_cart/ask_to_modify_cart.json` line 8 — google_adk_json: tool: ask_to_modify_cart — Confirm with the customer that it is ok to modify their cart

**get_available_planting_times (`get_available_planting_times`)**
- Adapter: `google_adk_json`
- Detected at: `tools/get_available_planting_times/get_available_planting_times.json` line 8 — google_adk_json: tool: get_available_planting_times — Retrieves available planting service time slots for a given 

**get_landscaping_quote (`get_landscaping_quote`)**
- Adapter: `google_adk_json`
- Detected at: `tools/get_landscaping_quote/get_landscaping_quote.json` line 8 — google_adk_json: tool: get_landscaping_quote — Gets a quote for the landscaping services that the user requ

**get_product_recommendations (`get_product_recommendations`)**
- Adapter: `google_adk_json`
- Detected at: `tools/get_product_recommendations/get_product_recommendations.json` line 9 — google_adk_json: tool: get_product_recommendations — Performs a lookup on the flower or plant details and then pr

**greeting (`greeting`)**
- Adapter: `google_adk_json`
- Detected at: `tools/greeting/greeting.json` line 9 — google_adk_json: tool: greeting — A static default greeting that is sent to the user.

**request_image_upload (`request_image_upload`)**
- Adapter: `google_adk_json`
- Detected at: `tools/request_image_upload/request_image_upload.json` line 8 — google_adk_json: tool: request_image_upload — Asks the user to share an image that can be used to identify

**schedule_planting_service (`schedule_planting_service`)**
- Adapter: `google_adk_json`
- Detected at: `tools/schedule_planting_service/schedule_planting_service.json` line 8 — google_adk_json: tool: schedule_planting_service — Schedules a planting service appointment.

Args:
    custome

**crm_service (`toolset_crm_service`)**
- Adapter: `google_adk_json`
- Detected at: `toolsets/crm_service/crm_service.json` line 3 — google_adk_json: toolset: crm_service

**update_cart (`update_cart`)**
- Adapter: `google_adk_json`
- Detected at: `tools/update_cart/update_cart.json` line 9 — google_adk_json: tool: update_cart — Update the user's cart based on their selections.

**update_salesforce_crm (`update_salesforce_crm`)**
- Adapter: `google_adk_json`
- Detected at: `tools/update_salesforce_crm/update_salesforce_crm.json` line 8 — google_adk_json: tool: update_salesforce_crm — Updates the Salesforce CRM with the most recent conversation

### Deployment Details

**generic (`deployment_generic`)**
- `gcloud` — `webapp/app.py`:74, `test_api.py`:7, `test_structure.py`:7
- `deployment` — `app.json`:265, `response.json`:190
- Source tiers: code, iac

### Privileges

- **code_execution**: scope=`code_execution`
- **filesystem_write**: scope=`filesystem_write`
- **network_out**: scope=`network_out`

## Dependencies

| Name | Version | Group | License |
| --- | --- | --- | --- |
| python-dotenv | >=1.0,<2 | runtime |  |

## Node Type Breakdown

| Type | Count |
| --- | --- |
| AGENT | 3 |
| AUTH | 1 |
| DEPLOYMENT | 1 |
| GUARDRAIL | 2 |
| MODEL | 2 |
| PRIVILEGE | 3 |
| PROMPT | 5 |
| TOOL | 13 |
