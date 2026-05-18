from typing import Optional

def get_model_text(response: LlmResponse) -> str:
  """Gets the latest model response text."""
  all_text = []
  for part in response.content.parts:
    all_text.append(part.text)

  if len(all_text) > 0:
    return all_text[-1]
  else:
    return []

def get_tool_calls(response: LlmResponse) -> dict:
  """Get latest tool call details if they exist."""
  tool_calls = []
  for part in response.content.parts:
    if part.function_call:
      tool: dict = {}
      tool["name"] = part.function_call.name
      tool["args"] = part.function_call.args
      tool_calls.append(tool)

  return tool_calls

def respond(parts: List[Part]) -> LlmResponse:
  """Help method to format the LlmResponse class."""
  if not isinstance(parts, list):
    raise "`parts` must be list of type `Part`"
  return LlmResponse(content=Content(parts=parts, role="model"))

def build_text_part(text: str) -> Part:
  return Part(text=text)

def build_fc_part(tool_name: str, args: dict) -> Part:
  return Part(function_call=FunctionCall(name=tool_name, args=args))

def after_model_callback(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
  print(callback_context)
  model_text = get_model_text(llm_response)
  image_tool_called = callback_context.variables["request_image_tool_called"]

  if model_text:
    print(f"--- DEBUG ---\nMODEL TEXT: {model_text}")
    # model is asking for upload but we haven't marked the tool called yet
    # this is to protect infinite reasoning loop
    if "upload" in model_text and not image_tool_called:

      # we get the actual predicted tool calls for this turn to see if they exist
      # if no tool was called, then we'll force the tool call and mark the flag as True
      tool_calls = get_tool_calls(llm_response)
      if not tool_calls:
        print("DEBUG: Forcing `request_image_upload` tool call.")
        fc_part = build_fc_part(
          "request_image_upload",
          {"customer_id": "428765091"}
          )

        callback_context.variables["request_image_tool_called"] = True

        return respond([fc_part])

  # Returning None allows the LLM's actual response to be used.
  return None