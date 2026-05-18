def greeting() -> dict:
  """A static default greeting that is sent to the user."""
  # Keep the cart lookup inside the greeting tool because CES already proved
  # this first-turn tool flow works for this app; only the lookup args changed.
  customer_profile = context.variables["customer_profile"]
  customer_id = customer_profile.get("customer_id")
  profile_key = customer_profile.get("profile_key")
  account_number = customer_profile.get("account_number")

  cart_args = {}
  if customer_id:
    cart_args["customer_id"] = customer_id
  elif account_number:
    cart_args["account_number"] = account_number
  elif profile_key:
    cart_args["profile_key"] = profile_key

  res = tools.crm_service_get_cart_information(cart_args)

  FIRST_NAME = customer_profile["customer_first_name"]
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
