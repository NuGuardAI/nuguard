def get_product_recommendations(plant_to_search: str) -> str:
  """Performs a lookup on the flower or plant details and then provides recommendations based on that information."""

  # Here, we are going to chain together 2 tool calls deterministically. 
  # This is done via tool vs. asking the Agent to chain together the calls
  # to _ensure_ these are always done in this order.
  # tools.<tool_name>_<endpoint_name>({tool_args})
  search_res = tools.lookup_plant_details({"query": plant_to_search})

  # Makes a live network call to a tool that contains mock data for recommendations
  res = tools.crm_service_get_product_recommendations({})

  return {
      "search_results": search_res.json(),
      "recommendations": res.json()
  }