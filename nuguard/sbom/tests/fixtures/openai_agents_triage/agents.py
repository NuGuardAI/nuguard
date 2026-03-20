"""Customer support triage system using the OpenAI Agents SDK."""
from agents import Agent, function_tool


TRIAGE_INSTRUCTIONS = """You are a customer support triage agent. Your job is to:
- Understand the customer's issue
- Route them to the right specialist agent
- Never attempt to solve technical issues yourself

Available specialists: billing_agent, technical_agent, returns_agent
"""

BILLING_INSTRUCTIONS = """You are a billing specialist. You handle:
- Invoice questions
- Payment failures
- Subscription changes
- Refund requests

Be empathetic and resolve issues quickly.
"""

TECHNICAL_INSTRUCTIONS = """You are a technical support specialist. You handle:
- Login and authentication problems
- Feature questions
- Bug reports
- Integration issues

Always ask for error messages and reproduction steps.
"""


@function_tool
def lookup_account(user_id: str) -> dict:
    """Look up a customer account by user ID."""
    return {"user_id": user_id, "status": "active", "plan": "pro"}


@function_tool
def create_refund(order_id: str, reason: str) -> str:
    """Process a refund for an order."""
    return f"Refund initiated for order {order_id}: {reason}"


billing_agent = Agent(
    name="billing_agent",
    instructions=BILLING_INSTRUCTIONS,
    model="gpt-4o",
    tools=[lookup_account, create_refund],
)

technical_agent = Agent(
    name="technical_agent",
    instructions=TECHNICAL_INSTRUCTIONS,
    model="gpt-4o-mini",
    tools=[lookup_account],
)

triage_agent = Agent(
    name="triage_agent",
    instructions=TRIAGE_INSTRUCTIONS,
    model="gpt-4o",
    handoffs=[billing_agent, technical_agent],
)
