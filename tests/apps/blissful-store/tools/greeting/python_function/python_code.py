import json

def greeting() -> dict:
  """A static default greeting that is sent to the user."""
  # Deterministically call another tool from within the greeting tool.
  # The syntax for OpenAPI spec tools is:
  # tools.<tool_name>_<endpoint_name>({tool_args})
  res = tools.crm_service_get_cart_information({})

  FIRST_NAME = context.variables["customer_profile"]["customer_first_name"]
  PHONE_NUMBER = get_variable("telephony-caller-id")
  UUI_HEADERS = get_variable("uui-headers")

  if UUI_HEADERS:
    print(UUI_HEADERS)

  if PHONE_NUMBER != " ":
    return {
    "greeting": f"Hi there! Welcome to Blissful Garden! Is this {FIRST_NAME}? I see you're calling from {PHONE_NUMBER} and your UUI Header info is {UUI_HEADERS}",
    "cart_information": res.json()
    }

  else:
    return {
      "greeting": f"Hi there! Welcome to Blissful Garden! Is this {FIRST_NAME}?",
      "cart_information": res.json()
      }
