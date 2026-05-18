# Marketing Campaign Assistant - Google ADK Tutorial Project

This repository contains the code for the Marketing Campaign Assistant project, built as part of a tutorial series on Google's Agent Development Kit (ADK).

**Part 1 of this series focuses on building the core multi-agent logic using Python and Google ADK, and running/visualizing it locally using the ADK command-line interface and web development UI.**

Watch the tutorial video here: [https://www.youtube.com/watch?v=/r-JsrEoctCQ](https://www.youtube.com/watch?v=/r-JsrEoctCQ)

## Project Description

The Marketing Campaign Assistant is a multi-agent system designed to automate the initial steps of creating a marketing campaign brief. It takes a product idea as input and uses a workflow of specialized AI agents to:

1.  Research market trends and target audience.
2.  Craft key messaging.
3.  Write ad copy variations.
4.  Suggest visual concepts.
5.  Format the results into a cohesive brief.

This project demonstrates how to leverage Google ADK's code-first approach and workflow orchestration capabilities to build agents that collaborate on a complex task.

## Features Covered in Part 1

*   Defining specialized `LlmAgent` components.
*   Orchestrating agents in a specific sequence using a `SequentialAgent` workflow.
*   Using built-in tools (like Google Search).
*   Passing state/information between agents.
*   Running the agent locally via the ADK CLI (`adk run`).
*   Visualizing the agent's execution flow and state using the ADK Web UI (`adk web`).

## Prerequisites

*   Python 3.7+
*   pip (Python package installer)
*   Access to an LLM provider (like Google AI Studio/Vertex AI, OpenAI, etc.) and an associated API Key. This tutorial uses Google's Gemini models via the Google AI client library, which is integrated with ADK.
*   A Google API Key with access to Gemini models. You can obtain one from [Google AI Studio](https://aistudio.google.com/).
*   Basic familiarity with Python.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/AhsanAyaz/marketing-agents-adk
    cd marketing-agents-adk
    ```
2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv .venv
    ```
3.  **Activate the virtual environment:**
    *   On macOS and Linux:
        ```bash
        source venv/bin/activate
        ```
    *   On Windows:
        ```bash
        .\venv\Scripts\activate
        ```
4.  **Install dependencies:**
    ```bash
    pip install -r backend/requirements.txt
    ```
    *(Note: The `requirements.txt` is located inside the `backend` folder, as Part 2 will add a frontend folder at the top level.)*
5.  **Set up your API Key:**
    *   Create a file named `.env` in the **directory** `marketing_campaign_agent`.
    *   Add your Google API Key to this file, using the environment variable name expected by the Google AI client library (usually `GOOGLE_API_KEY`).
        ```env
        GOOGLE_API_KEY='YOUR_ACTUAL_GOOGLE_API_KEY'
        ```
    *   Replace `'YOUR_ACTUAL_GOOGLE_API_KEY'` with your key.

## Project Structure (Part 1)

```
marketing-agents-adk/
├── marketing_campaign_agent/
│   ├── __init__.py         # Package initialization
    └── .env                # Stores your API key (not committed to git)
│   ├── agent.py            # Defines all LlmAgents and the SequentialAgent workflow
│   ├── requirements.txt    # Project dependencies
│   └── instruction.py      # Text files containing detailed instructions for each agent
```

## How to test the agent locally (Part 1)
1. Run the server:
```bash
./serve.sh
```
2. Run the agent test:
```bash
./agentic-test.sh
```
Check the logs and output in the reports folder.

## How to Run the Agent (Original)

You can run and interact with the agent as explained in the video:
## **Using the ADK Web UI (`adk web`)**
    *   Ensure your virtual environment is activated and the `.env` file is set up correctly in the project root.
    *   Navigate to the project root directory (`marketing-agents-adk`).
    *   Run the ADK web command:
        ```bash
        adk web
        ```
    *   The command will start a local web server and provide a URL (usually `http://localhost:8000`).
    *   Open this URL in your web browser.
    *   In the Web UI, select `marketing_campaign_agent` from the dropdown on the left.
    *   You can then type messages, view agent responses, and explore the "Events" tab to see the internal workflow execution, including which sub-agents were called and in what order.

## Future Development (Part 2)

In the next part of this tutorial series, we will:

*   Deploy our agent to Google Cloud

Stay tuned!

## Contributing

If you find any issues or have suggestions for improvements, feel free to open an issue or submit a pull request.

## License

MIT
