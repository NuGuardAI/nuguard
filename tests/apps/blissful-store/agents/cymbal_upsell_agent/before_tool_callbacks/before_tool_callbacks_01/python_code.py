def before_tool_callback(tool: Tool, input: dict[str, Any], callback_context: CallbackContext) -> Optional[dict[str, Any]]:
  """Check for the next tool call and then act accordingly."""
  if tool.name == "approve_discount":
    amount = input.get("value", None)
    discount_type = input.get("type", "percentage")
    manager_approved = callback_context.variables["manager_approved"]
      
    if amount > 20 and discount_type == "percentage" and not manager_approved:
        return {"result": "You shouldn't be approving this, you need to get manager approval per the business rules. Call `ask_for_approval` tool."}
    
    return {"result": "Call `apply_discount_to_service` tool."}
  
  elif tool.name == "apply_discount_to_service":
    manager_approved = callback_context.variables["manager_approved"]
    if not manager_approved:
      return {"result": "You need manager approval before applying this discount."}
      
  elif tool.name == "ask_for_approval":
    amount = input.get("value", None)
    discount_type = input.get("type", "percentage")
    
    if amount <= 20 and discount_type == "percentage":
        return {"result": "You can approve this discount; no manager needed."}

  return None