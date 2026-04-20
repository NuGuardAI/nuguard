# ADK Callback Showcase: Real-World Examples

This project provides a collection of isolated examples demonstrating various use cases for Agent Development Kit (ADK) callbacks. Each `example_` prefixed folder contains a self-contained agent (`agent.py`) and a `README.md` explaining the specific callbacks being showcased and how to test them.

## Overall Project Goals:

- Illustrate practical applications of each of the six main ADK callback types.
- Provide clear, runnable examples for educational purposes.
- Show how callbacks can make agents more robust, secure, user-friendly, and efficient.

## Prerequisites:

- Python 3.x
- Google Agent Development Kit (ADK) installed.
- Access to a Gemini model (e.g., `gemini-2.0-flash`).
- `requests` library (for some examples).

## Setup:

1.  Clone this repository (or create the file structure).
2.  Create and activate a Python virtual environment:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```
3.  Install the common requirements:
    ```bash
    pip install -r requirements.txt
    ```
4.  Ensure your Google Cloud authentication is set up if required by your Gemini model.

## Examples:

Navigate into each example folder to find specific instructions in its `README.md`.

- **`example_01_agent_lifecycle_logging/`**: Demonstrates `before_agent_callback` and `after_agent_callback` for detailed session logging and metrics (e.g., request count, duration).
- **`example_02_model_input_sanitization/`**: Uses `before_model_callback` to sanitize user input by redacting sensitive information (like mock PII) before it reaches the LLM.
- **`example_03_model_response_enhancement/`**: Employs `after_model_callback` to parse a structured response from the LLM (even if the LLM provided free-form text) or to add helpful quick links.
- **`example_04_tool_arg_validation_modification/`**: Showcases `before_tool_callback` for validating tool arguments (e.g., date formats for a booking tool) and modifying them for consistency.
- **`example_05_tool_response_transformation_caching/`**: Uses `before_tool_callback` for response caching (e.g., for an external currency conversion API) and `after_tool_callback` to transform the tool's raw output into a more user-friendly format.

To run any example:

```bash
cd <example_folder_name>  # e.g., cd example_01_agent_lifecycle_logging
adk web
```

---

The content within each `agent.py`, `__init__.py`, and the individual `README.md` files inside `example_01_...`, `example_02_...` etc., would remain the same as previously detailed, as the core logic and explanations for each specific example are unchanged. The key correction is the folder naming convention.
