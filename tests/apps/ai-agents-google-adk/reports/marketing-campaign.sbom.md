# SBOM Report: /workspaces/nuguard-private/tests/apps/ai-agents-google-adk

**Generated:** 2026-04-04T20:52:34Z  
**Schema version:** 1.4.0  

## Summary

| Field | Value |
| --- | --- |
| AI nodes | 49 |
| Dependencies | 7 |
| Use case | This application uses 22 agents to assist with marketing campaign development, social media strategy, and market research, leveraging search-based retrieval. It supports image inputs and generates content for platforms like Instagram and LinkedIn. The system does not support voice or video modalities. |
| Frameworks | google-adk, google_adk |
| Modalities | TEXT, IMAGE |

## AI Components

| Name | Type | Confidence | Details |
| --- | --- | --- | --- |
| AdCopyWriter | AGENT | 88% | google-adk |
| AdviceGeneratorAgent | AGENT | 88% | google-adk |
| CampaignBriefFormatter | AGENT | 88% | google-adk |
| currency_converter_agent | AGENT | 88% | google-adk |
| input_sanitizer_agent | AGENT | 88% | google-adk |
| InstagramReelScriptAgent | AGENT | 96% | google-adk |
| lifecycle_logger_agent | AGENT | 88% | google-adk |
| LinkedInPostsAgent | AGENT | 96% | google-adk |
| MarketingCampaignAssistant | AGENT | 88% | google-adk |
| MarketResearcher | AGENT | 88% | google-adk |
| meeting_scheduler_agent | AGENT | 88% | google-adk |
| MessagingStrategist | AGENT | 88% | google-adk |
| PostAgent | AGENT | 88% | google-adk |
| PostsAgent | AGENT | 96% | google-adk |
| PostsMergerAgent | AGENT | 96% | google-adk |
| ProblemAnalyzerAgent | AGENT | 88% | google-adk |
| ResearchAgent | AGENT | 96% | google-adk |
| SocialMediaAgent | AGENT | 96% | google-adk |
| StructuredConsultationAgent | AGENT | 88% | google-adk |
| tools_agent | AGENT | 88% | google-adk |
| travel_response_enhancer_agent | AGENT | 88% | google-adk |
| VisualSuggester | AGENT | 88% | google-adk |
| generic | AUTH | 62% |  |
| sqlite | DATASTORE | 44% |  |
| generic | DEPLOYMENT | 99% |  |
| framework:google_adk | FRAMEWORK | 100% | google_adk |
| framework:llm_clients | FRAMEWORK | 100% | llm_clients |
| gemini-2.0-flash | MODEL | 100% | google |
| network_out | PRIVILEGE | 100% | network_out |
| AdviceGeneratorAgent Instructions | PROMPT | 77% | " You are a helpful AI assistant that provides initial guidance based on a recomm…" · role=system |
| currency_converter_agent Instructions | PROMPT | 77% | "You are a currency converter. When asked to convert currency, use the convert_cu…" · role=system |
| input_sanitizer_agent Instructions | PROMPT | 77% | "You are a helpful general knowledge assistant." · role=system |
| InstagramReelScriptAgent Instructions | PROMPT | 84% | "     You are an Instagram reel script generator. You will be given a topic with …" · role=system |
| lifecycle_logger_agent Instructions | PROMPT | 77% | "You are an echo agent. Repeat the user's message." · role=system |
| LinkedInPostsAgent Instructions | PROMPT | 84% | "     You are a LinkedIn post generator. You will be given a topic with researche…" · role=system |
| meeting_scheduler_agent Instructions | PROMPT | 77% | "You are a meeting scheduling assistant. When asked to schedule a meeting, gather…" · role=system |
| PostAgent Instructions | PROMPT | 77% | "         You are a helpful assistant that can respond about the user and their p…" · role=system |
| PostsAgent Instructions | PROMPT | 84% | "An agent that generates social media posts by using the linkedIn and Instagram a…" · role=system |
| PostsMergerAgent Instructions | PROMPT | 84% | "         You are an AI Assistant responsible for combining linkedin and instagra…" · role=system |
| ProblemAnalyzerAgent Instructions | PROMPT | 77% | " You are an expert AI assistant that analyzes user queries to understand their c…" · role=system |
| ResearchAgent Instructions | PROMPT | 84% | "     You are a research assistant. You will be given a topic and you will resear…" · role=system |
| SocialMediaAgent Instructions | PROMPT | 84% | "An agent that generates social media posts by using the research agent and the p…" · role=system |
| tools_agent Instructions | PROMPT | 77% | "   You are a helpful assistant that can use the following tools:   - get_current…" · role=system |
| travel_response_enhancer_agent Instructions | PROMPT | 77% | "You are a helpful travel assistant. If a user asks to book a flight, confirm the…" · role=system |
| convert_currency_tool | TOOL | 100% | google-adk |
| get_current_date_and_time | TOOL | 100% | google-adk |
| get_randomuser_from_ramdomuserme | TOOL | 100% | google-adk |
| google_search | TOOL | 100% | google-adk |
| schedule_meeting_tool | TOOL | 82% | google-adk |

### Prompt Details

**AdviceGeneratorAgent Instructions**
- Role: `system`
- Content:  You are a helpful AI assistant that provides initial guidance based on a recommended consultant type and identified user issues.  Based on the provided input {problem_analysis_result} generate the following in a structured JSON format: 1.  'suitability_explanation': A brief (1-2 sentences) explanat…
- Template variables: `problem_analysis_result`

**currency_converter_agent Instructions**
- Role: `system`
- Content: You are a currency converter. When asked to convert currency, use the convert_currency_tool. Based on the tool's output (look for 'result_summary' or 'error_message' in the tool's returned dictionary), provide a clear and concise answer to the user.

**input_sanitizer_agent Instructions**
- Role: `system`
- Content: You are a helpful general knowledge assistant.

**InstagramReelScriptAgent Instructions**
- Role: `system`
- Content:      You are an Instagram reel script generator. You will be given a topic with researched summary from "research_summary" output, and you will generate a script for an Instagram reel about it.     The script should be engaging, fast paced, and relevant to the topic.     The script should have a pri…

**lifecycle_logger_agent Instructions**
- Role: `system`
- Content: You are an echo agent. Repeat the user's message.

**LinkedInPostsAgent Instructions**
- Role: `system`
- Content:      You are a LinkedIn post generator. You will be given a topic with researched summary from "research_summary" output, and you will generate a LinkedIn post about it.         The post should be professional, engaging, and relevant to the topic.         The post should have a primary hook, not mor…

**meeting_scheduler_agent Instructions**
- Role: `system`
- Content: You are a meeting scheduling assistant. When asked to schedule a meeting, gather the date (YYYY-MM-DD), topic, attendees (as a list), and a time preference (e.g., '10:00 AM', 'afternoon', 'morning'). Then use the 'schedule_meeting_tool'.

**PostAgent Instructions**
- Role: `system`
- Content:          You are a helpful assistant that can respond about the user and their post preferences.      The information about the user and their post preferences is given in the state context.     Name: {user_name}     Post Preferences: {user_post_preferences} 
- Template variables: `user_name`, `user_post_preferences`

**PostsAgent Instructions**
- Role: `system`
- Content: An agent that generates social media posts by using the linkedIn and Instagram agents

**PostsMergerAgent Instructions**
- Role: `system`
- Content:          You are an AI Assistant responsible for combining linkedin and instagram reels script into a structured output.  Your primary task is to merge the posts generated by the LinkedIn and Instagram agents into a single output. Clearly mentioning the platform for each post.         Input Summarie…
- Template variables: `linkedIn_post`, `instagram_reel_script`, `linkedIn_post`, `instagram_reel_script`

**ProblemAnalyzerAgent Instructions**
- Role: `system`
- Content:  You are an expert AI assistant that analyzes user queries to understand their core problem and recommend an appropriate type of consultant. Based on the user's query, identify the primary issues the user is facing. Then, determine the most suitable consultant type Output a JSON object with the reco…

**ResearchAgent Instructions**
- Role: `system`
- Content:      You are a research assistant. You will be given a topic and you will research on it. Then you will provide a summary of the research. 

**SocialMediaAgent Instructions**
- Role: `system`
- Content: An agent that generates social media posts by using the research agent and the posts agent

**tools_agent Instructions**
- Role: `system`
- Content:    You are a helpful assistant that can use the following tools:   - get_current_date_and_time: Returns the current date and time.   - get_randomuser_from_ramdomuserme: Returns a random user from randomuser.me API. 

**travel_response_enhancer_agent Instructions**
- Role: `system`
- Content: You are a helpful travel assistant. If a user asks to book a flight, confirm the booking with made-up details including a flight number (e.g., BA245, AF1800), origin city, destination city, and date (YYYY-MM-DD format). If a user asks about policies, provide a general answer. Example of flight booki…

### Datastore Details

**sqlite**

### Model Details

| Model | Provider | Family | API Endpoint |
| --- | --- | --- | --- |
| gemini-2.0-flash | google | gemini | https://generativelanguage.googleapis.com |

### Tool Details

**convert_currency_tool (`google_adk_tool_convert_currency_tool`)**
- Adapter: `google_adk`
- Detected at: `agents_and_callbacks/example_05_tool_response_transformation_caching/agent.py` line 204 — google_adk: tools=[..., convert_currency_tool, ...]

**get_current_date_and_time (`google_adk_tool_get_current_date_and_time`)**
- Adapter: `google_adk`
- Detected at: `tools_agent/agent.py` line 37 — google_adk: tools=[..., get_current_date_and_time, ...]

**get_randomuser_from_ramdomuserme (`google_adk_tool_get_randomuser_from_ramdomuserme`)**
- Adapter: `google_adk`
- Detected at: `tools_agent/agent.py` line 37 — google_adk: tools=[..., get_randomuser_from_ramdomuserme, ...]

**google_search (`google_adk_tool_google_search`)**
- Adapter: `google_adk`
- Detected at: `deploying_agents/social_posts_agent/agent.py` line 22 — google_adk: tools=[..., google_search, ...]
- Detected at: `marketing_campaign_agent/agent.py` line 24 — google_adk: tools=[..., google_search, ...]
- Detected at: `multi_model/agent.py` line 14 — google_adk: tools=[..., google_search, ...]

**schedule_meeting_tool (`google_adk_tool_schedule_meeting_tool`)**
- Adapter: `google_adk`
- Detected at: `agents_and_callbacks/example_04_tool_arg_validation_modification/agent.py` line 73 — google_adk: tools=[..., schedule_meeting_tool, ...]

### Deployment Details

**generic (`deployment_generic`)**
- `deployment` — `deploying_agents/actions.py`:23, `deploying_agents/run-flow.py`:42
- `Deployment` — `deploying_agents/cleanup.py`:45

### Privileges

- **network_out**: scope=`network_out`

## Dependencies

| Name | Version | Group | License |
| --- | --- | --- | --- |
| google-adk | ==1.28.0 | runtime |  |
| google-generativeai |  | runtime |  |
| python-dotenv |  | runtime |  |
| litellm |  | runtime |  |
| pydantic |  | runtime |  |
| google-cloud-aiplatform | [adk,agent_engines] | runtime |  |
| @google/adk | ^0.6.1 | runtime |  |

## Node Type Breakdown

| Type | Count |
| --- | --- |
| AGENT | 22 |
| AUTH | 1 |
| DATASTORE | 1 |
| DEPLOYMENT | 1 |
| FRAMEWORK | 2 |
| MODEL | 1 |
| PRIVILEGE | 1 |
| PROMPT | 15 |
| TOOL | 5 |
