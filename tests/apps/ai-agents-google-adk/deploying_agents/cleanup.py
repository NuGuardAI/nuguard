import os
import sys

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

import vertexai
from actions import delete_all_sessions, delete_session, list_deployments
from google.api_core import exceptions
from vertexai import agent_engines

PROJECT_ID = os.environ.get("PROJECT_ID", "multiversity-418607")
LOCATION = os.environ.get("LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "gs://social-posts-agent-test-1")

vertexai.init(
    project=PROJECT_ID,
    location=LOCATION,
    staging_bucket=STAGING_BUCKET,
)

if __name__ == "__main__":
    # Example usage
    print("Listing deployments...")

    deployments = list_deployments()

    if not deployments:
        print("No deployments found. Exiting.")
        sys.exit(1)

    for remote_app in deployments:
        resource_id = remote_app.resource_name
        USER_ID = "123"

        print("\nCleaning up: Deleting all sessions...")
        delete_all_sessions(resource_id, USER_ID)

        print("-" * 50)
        print("All sessions deleted.")

        agent_engines.delete(resource_id)

        print(f"Deployment {resource_id} deleted.")
