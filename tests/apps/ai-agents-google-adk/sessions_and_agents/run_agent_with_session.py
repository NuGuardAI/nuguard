import uuid

from agent import root_agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

session_service = InMemorySessionService()
state_context = {
    "user_name": "Ahsan",
    "user_post_preferences": """
        - LinkedIn: Professional, engaging, and relevant to the topic.
            should have a primary hook, not more than 60 characters.
            should have a line break after the hook.
            should have a post-hook that is either supporting the hook or completely inverse of the hook to grab attention.
            should be in a conversational tone and should be easy to read.
            should have bullet points in the post to make it easy to read.
            should have actionable items in the post to make it easy to follow.
            should have a question to engage the audience.
            should ask the audience to share their thoughts in the comments. And to repost.
            should use emojis to make the post more engaging.
            should use hashtags to make the post more discoverable.
        - Instagram: Engaging, fast paced, and relevant to the topic.
            should have a primary hook, which grabs the attention of the audience.
            should have a call to action at the end.
        """,
}

SESSION_ID = str(uuid.uuid4())
USER_ID = "ahsanayaz"
APP_NAME = "Social Media Post Generator"

session = session_service.create_session(
    app_name=APP_NAME,
    user_id=USER_ID,
    session_id=SESSION_ID,
    state=state_context,
)

print("Session ID:", session.id)

runner = Runner(
    agent=root_agent,
    session_service=session_service,
    app_name=APP_NAME,
)

user_query = types.Content(
    role="user",
    parts=[
        types.Part(
            text="What does the user want at the beginning of the post?",
        )
    ],
)

for event in runner.run(
    user_id=USER_ID,
    session_id=SESSION_ID,
    new_message=user_query,
):
    if event.is_final_response():
        if event.content and event.content.parts:
            print("Final response:", event.content.parts[0].text)

session = session_service.get_session(
    app_name=APP_NAME,
    user_id=USER_ID,
    session_id=SESSION_ID,
)

print("\n\n\nSession state:", session.state)

for key, value in session.state.items():
    print(f"{key}: {value}")
