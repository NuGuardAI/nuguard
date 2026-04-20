def respond(text: str) -> LlmResponse:
  """helper function to respond to the user with the given text"""
  return LlmResponse(content=Content(parts=[Part(text=text)], role="model"))

def handle_end_session(context: CallbackContext, tool: Tool) -> dict:
  if tool.name == "end_session":
    print(context)
  elif tool.name == "transfer_to_agent":
    print(context)
  
def after_tool_callback(tool: Tool, input: dict[str, Any], callback_context: CallbackContext, tool_response: dict[str, Any]) -> Optional[dict[str, Any]]:

  # Set the flag to true if not already set and the tool was called
  image_tool_called = callback_context.variables["request_image_tool_called"]
  if not image_tool_called:
    if tool.name == "request_image_upload":
      callback_context.variables["request_image_tool_called"] = True

  res = handle_end_session(callback_context, tool)

  print(tool_response)
  # Returning None allows the tool's actual result to be used.
  return None