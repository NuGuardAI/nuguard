from typing import List, Optional

def get_all_text_parts(contents: Optional[List[Any]]) -> List[str]:
  text_parts: List[str] = []

  if not contents:
    return text_parts

  for content in contents:
    parts = getattr(content, "parts", [])
    for part in parts:
      text = getattr(part, "text", None)
      if text:
        text_parts.append(text)

  return text_parts

def build_text_part(text: str) -> Part:
  return Part(text=text)

def build_tool_part(tool_name: str, args: dict) -> Part:
  return Part(function_call=FunctionCall(name=tool_name, args=args))

def get_last_utterance(request: LlmRequest) -> Optional[str]:
  """Extract the last utterance from the request contents."""
  parts = get_all_text_parts(request.contents)
  # Returns None if all_parts is empty to avoid IndexError
  return parts[-1] if parts else None

def respond(parts: List[Part]) -> LlmResponse:
    """Helper method to format the LlmResponse class."""
    if not isinstance(parts, list):
        raise TypeError("`parts` must be list of type `Part`")
    
    return LlmResponse(content=Content(parts=parts, role="model"))

def before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
  """Callback to validate the user query before sending it to the model."""
  user_query = get_last_utterance(llm_request)

  # Manual Guardrail: override response if specific keywords are found
  if user_query and "system instructions" in user_query.lower():
    text_part = build_text_part("I'm sorry but I'm afraid I can't comply with that request.")
    return respond([text_part])

  # Check for user inactivity and end session if required
  counter = callback_context.variables["no_input_counter"]

  print(f"BEFORE MODEL COUNTER: {counter}")
  if counter >= 2:
    text_part = build_text_part("We're ending the session due to no response.")
    tool_part = build_tool_part(
      tool_name="end_session",
      args={
        "reason": "user_inactivity",
        "session_escalated": False,
        "params": {"session_id": callback_context.session_id}
      }
    )

    return respond([text_part, tool_part])

  return None