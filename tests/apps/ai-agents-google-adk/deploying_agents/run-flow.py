import os
import sys
import time

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

import vertexai
from actions import (
    create_session,
    delete_session,
    get_session,
    list_deployments,
    list_sessions,
    send_message,
)

# Get deployment settings from environment variables
PROJECT_ID = os.environ.get("PROJECT_ID", "multiversity-418607")
LOCATION = os.environ.get("LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "gs://social-posts-agent-test-1")

vertexai.init(
    project=PROJECT_ID,
    location=LOCATION,
    staging_bucket=STAGING_BUCKET,
)

if __name__ == "__main__":
    print("Listing deployments...")
    deployments = list_deployments()

    if not deployments:
        print("No deployments found. Exiting.")
        sys.exit(1)

    remote_app = deployments[0]
    resource_id = remote_app.resource_name
    user_id = "123"
    session_id = None

    print(f"Using deployment: {resource_id}")

    # List all sessions
    print("\nListing sessions...")
    sessions = list_sessions(resource_id, user_id)
    print(f"sessions: {sessions}")

    if not sessions:
        print(f"No sessions found. Creating session for user {user_id}...")
        session = create_session(resource_id, user_id)
        time.sleep(1)

        if not session:
            print("Failed to create session. Exiting.")
            sys.exit(1)
        else:
            session_id = session["id"]
            print(f"Session created: {session_id}")
    else:
        print(f"Found {len(sessions)} sessions.")
        session = sessions[0]
        # Get the first session ID
        session_id = session["id"]
        print(f"Using existing session ID: {session_id}")
        # Get the session object
    # Get session details
    print("\nGetting session details...")
    session_info = get_session(resource_id, user_id, session_id)

    if not session_info:
        print("Failed to get session details. Continuing with other operations.")
        sys.exit(1)

    print("\nSending message...")

    send_message(
        resource_id,
        user_id,
        session_id,
        message="Create a post for what's new in Angular v20?",
    )

    # Delete the session we created
    # print("\nDeleting session...")
    # delete_session(resource_id, user_id, session_id)
