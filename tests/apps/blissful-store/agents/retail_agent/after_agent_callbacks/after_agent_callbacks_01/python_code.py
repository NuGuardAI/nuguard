def get_all_text_parts(content: Content) -> List[str]:
  """helper method to get all text parts from context."""
  print(f"CONTENT {content}")
  text_parts: List[str] = []

  if not content:
    return text_parts

  parts = getattr(content, "parts", [])
  for part in parts:
    text = getattr(part, "text", None)
    if text:
      text_parts.append(text)

  return text_parts

def get_user_activity_signal(context: CallbackContext) -> str:
  parts = get_all_text_parts(context.user_content)
  for part in parts:
    if "no user activity detected" in part:
      return True

  return False

def get_user_text(context: CallbackContext) -> str:
  """helper function to get the last user utterance from the callback context"""
  parts = get_all_text_parts(context.user_content)
  return parts[-1]
  
def respond(parts: List[Part]) -> Content:
  """Helper method to format the Content class."""
  if not isinstance(parts, list):
    raise TypeError("`parts` must be list of type `Part`")
  
  return Content(parts=parts, role="model")
  
def after_agent_callback(callback_context: CallbackContext) -> Optional[Content]:
  # Check for User Inactive Signals
  user_inactive = get_user_activity_signal(callback_context)
  counter = callback_context.variables["no_input_counter"]
  print(f"COUNTER: {counter}")

  if user_inactive:
    callback_context.variables["no_input_counter"] = counter + 1
    counter = callback_context.variables["no_input_counter"]
    print(f"COUNTER: {counter}")

  elif not user_inactive:
    callback_context.variables["no_input_counter"] = 0

  # Returning None allows the agent's generated result to pass through.
  return None