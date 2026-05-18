import os

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import Agent

root_agent = Agent(
    name="PostAgent",
    description="An agent that knows some things about the user and their posts preferences",
    model=os.environ.get("GOOGLE_GENAI_MODEL"),
    instruction="""
        You are a helpful assistant that can respond about the user and their post preferences.

    The information about the user and their post preferences is given in the state context.
    Name: {user_name}
    Post Preferences: {user_post_preferences}
    """,
)
