# PyRIT Endpoint Techniques and Schema Handling

This note summarizes how PyRIT can target different endpoint styles for red-teaming and how it determines the request and response shapes it uses.

## Endpoint Support Matrix

| Technique / Target Type | PyRIT Target(s) | What It Can Red-Team | How Request Shape Is Determined | How Response Is Parsed | Relevant Docs / Code |
| --- | --- | --- | --- | --- | --- |
| OpenAI-compatible chat completion APIs | `OpenAIChatTarget` | OpenAI, Azure OpenAI chat completions, and other OpenAI-compatible providers such as Ollama, Groq, LM Studio | PyRIT uses the target class's built-in OpenAI chat request construction. You provide endpoint, model, auth, and optional metadata such as JSON output settings. | PyRIT parses the standard OpenAI-compatible chat response format in the target implementation. | [OpenAI Chat Target docs](https://azure.github.io/PyRIT/code/targets/1_openai_chat_target.html) |
| OpenAI Responses API endpoints | `OpenAIResponseTarget` | OpenAI Responses API style endpoints, including tool-calling and multimodal workflows | PyRIT builds the request body in code from `Message` / `MessagePiece` objects into the Responses API `input` structure. | PyRIT parses response items and tool-call artifacts in the target implementation. | [OpenAI Responses Target docs](https://azure.github.io/PyRIT/code/targets/2_openai_responses_target.html), [OpenAIResponseTarget code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/openai/openai_response_target.py) |
| Azure ML managed online endpoints | `AzureMLChatTarget` | Azure ML-hosted chat or inference endpoints, including Hugging Face models deployed on Azure ML | PyRIT uses the Azure ML target's built-in payload format. For AML-hosted models, the docs say the JSON body can be taken from the model catalog sample input schema. | PyRIT parses the AML response shape expected by the target implementation. The AML docs also note you can infer schema details from a bad-request response. | [AML Chat Targets docs](https://azure.github.io/PyRIT/code/targets/7_aml_chat_targets.html), [Score Azure ML Managed Online Endpoint docs](https://azure.github.io/PyRIT/deployment/score_aml_endpoint.html), [AzureMLChatTarget code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/azure_ml_chat_target.py) |
| Hugging Face hosted endpoints | `HuggingFaceChatTarget`, `HuggingFaceEndpointTarget` | Hugging Face inference-style endpoints supported by PyRIT target classes | PyRIT uses target-specific code for the endpoint family rather than discovering schema dynamically. | PyRIT parses the response format expected by the Hugging Face target class. | [PyRIT prompt target exports](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/__init__.py), [HuggingFaceEndpointTarget code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/hugging_face/hugging_face_endpoint_target.py) |
| Generic arbitrary HTTP endpoints | `HTTPTarget` | Custom REST endpoints, nonstandard LLM APIs, and even flows captured from Burp / DevTools | You provide the raw HTTP request template and include a prompt placeholder such as `{PROMPT}`. PyRIT injects the prompt into that template. | You supply a callback function. PyRIT includes helpers for JSON path extraction and regex extraction. | [HTTP Target docs](https://azure.github.io/PyRIT/code/targets/10_http_target.html), [HTTPTarget code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/http_target/http_target.py), [HTTP callback helpers](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/http_target/http_target_callback_functions.py) |
| Generic HTTP APIs via `httpx` client | `HTTPXAPITarget` | APIs where a normal structured HTTP client is more convenient than a raw request capture | You configure the request through the target and `httpx` client settings rather than OpenAPI import or schema discovery. | Parsing is handled by the target configuration and implementation. | [PyRIT prompt target exports](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/__init__.py), [HTTPXAPITarget code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/http_target/httpx_api_target.py) |
| Browser / app interaction targets | `PlaywrightTarget`, `PlaywrightCopilotTarget`, `WebSocketCopilotTarget` | Web apps, copilots, and interactive targets that are not exposed as clean chat APIs | Schema is not inferred as an API contract. The target logic interacts with the app transport or browser workflow directly. | Parsing depends on the target's browser, websocket, or app-specific extraction logic. | [HTTP Target section nav showing related prompt targets](https://azure.github.io/PyRIT/code/targets/10_http_target.html), [PyRIT prompt target exports](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/__init__.py) |
| Prompt safety / classification endpoints | `PromptShieldTarget` | Prompt shield and classifier-style endpoints used as part of scoring or defense evaluation | Request shape is implemented in the target class for that service. | Response parsing is implemented in the target class. | [PyRIT prompt target exports](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/__init__.py), [PromptShieldTarget code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/prompt_shield_target.py) |

## How PyRIT Learns the Request and Response Schema

### 1. Built-in targets use hardcoded schema knowledge

For first-class targets such as `OpenAIChatTarget`, `OpenAIResponseTarget`, `AzureMLChatTarget`, and Hugging Face targets, PyRIT does not appear to auto-discover the request and response schema from OpenAPI, Swagger, or runtime introspection.

Instead, the target class itself encodes:

- what request body to send
- what headers or auth shape to use
- what response fields to read

This is visible in the target implementations and in the docs examples:

- `OpenAIChatTarget` uses the standard chat-completions request format and supports OpenAI-compatible endpoints: [docs](https://azure.github.io/PyRIT/code/targets/1_openai_chat_target.html)
- `OpenAIResponseTarget` explicitly builds the Responses API `input` array in code: [code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/openai/openai_response_target.py)
- `AzureMLChatTarget` is implemented as a target for AML endpoint conventions: [code](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/azure_ml_chat_target.py)

### 2. Generic HTTP targets require the user to define the schema handling

For custom endpoints, PyRIT relies on the user to tell it both:

- how to format the request
- how to extract the response text

With `HTTPTarget`, you provide:

- a raw HTTP request template
- a prompt placeholder such as `{PROMPT}`
- a callback function to parse the returned content

The docs are explicit that for non-AOAI responses, the correct output path should come from the endpoint documentation or from manually testing the endpoint response:

- [HTTP Target docs](https://azure.github.io/PyRIT/code/targets/10_http_target.html)

PyRIT ships two helper parsing approaches:

- JSON field extraction via `get_http_target_json_response_callback_function(key="choices[0].message.content")`
- regex extraction via `get_http_target_regex_matching_callback_function(...)`

Source:

- [HTTP callback helpers](https://raw.githubusercontent.com/Azure/PyRIT/main/pyrit/prompt_target/http_target/http_target_callback_functions.py)

### 3. Azure ML is a partial exception because the platform may expose a sample schema

The Azure ML scoring docs explain that the request JSON body can be obtained from the Azure ML model catalog sample input schema. They also note that some schema details can be deduced from the error response after sending a bad request.

Source:

- [Score Azure ML Managed Online Endpoint docs](https://azure.github.io/PyRIT/deployment/score_aml_endpoint.html)

## Bottom Line

PyRIT supports multiple endpoint styles for red-teaming, including:

- OpenAI-compatible APIs
- OpenAI Responses API
- Azure ML endpoints
- Hugging Face endpoints
- arbitrary HTTP endpoints
- browser- and websocket-driven app targets

But PyRIT generally does **not** auto-discover API schema. In practice, it uses one of two techniques:

1. Built-in target class logic for known endpoint families
2. User-supplied request templates and response parsers for custom endpoints

