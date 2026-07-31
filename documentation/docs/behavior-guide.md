# NuGuard Behavior Engine

Static and dynamic behavioral validation for live AI applications. It's designed for AI developers who want to verify that their application behaves as intended — exercising every declared component, respecting cognitive policy boundaries, and handling sensitive user data correctly — before the app reaches production.

The engine takes an AI-SBOM, a target URL, and a Cognitive Policy, then automatically generates and executes multi-turn test scenarios against the running application, judging every turn with a 3-dimension rubric and producing structured findings with actionable remediation.

---

## Quick Start

Behavior testing needs a live target — point `nuguard init` at your running app, then run it.

<img src="assets/quickstart-3-behavior.svg" alt="nuguard init --target <your-app-url>. nuguard sbom generate --source <path-to-your-app> --output app.sbom.json. nuguard behavior --config nuguard.yaml --format markdown --output <output-path>." width="620">

**Common changes** (in `nuguard.yaml`, under `target:`):

- Target URL and endpoint — `target.url`, `target.endpoint` (default: auto-discovered from SBOM, fallback `/chat`)
- Request/response payload shape — `chat_payload_key` / `chat_response_key` / `chat_payload_extras` if your app doesn't use `{"message": "..."}` / `{"response": "..."}`
- Auth — `target.auth`: `bearer`, `api_key`, `basic`, or `login_flow`

### 📖 Need every flag?

[![Read the CLI Reference](https://img.shields.io/badge/→_Read_the_CLI_Reference-111111?style=for-the-badge)](cli-reference.md#nuguard-behavior)

### 🔀 Want to run a different test?

[![Back to Quick Start](https://img.shields.io/badge/←_Back_to_Quick_Start-111111?style=for-the-badge)](quick-start.md)

---