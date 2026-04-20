import time
from typing import Any, Dict, List, Optional

from google.api_core import exceptions
from vertexai import agent_engines


def list_deployments() -> List[Any]:
    """Lists all deployments.

    Returns:
        List of deployments, or empty list if none found or error occurs
    """
    try:
        deployments = list(
            agent_engines.list()
        )  # Convert generator to list immediately
        if not deployments:
            print("No deployments found.")
            return []

        print("Deployments:")
        for deployment in deployments:
            print(f"- {deployment.resource_name}")

        return deployments
    except exceptions.GoogleAPIError as e:
        print(f"Error listing deployments: {e}")
        return []


def create_session(resource_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Creates a new session for the specified user and resource.

    Args:
        resource_id: The ID of the resource/deployment
        user_id: The ID of the user

    Returns:
        The created session or None if an error occurs
    """
    try:
        deployment = agent_engines.get(resource_id)
        session = deployment.create_session(user_id=user_id)
        print(f"Session created: {session}")
        return session
    except exceptions.GoogleAPIError as e:
        print(f"Error creating session: {e}")
        return None


def list_sessions(resource_id: str, user_id: str) -> List[Any]:
    """Lists all sessions for the specified user and resource.

    Args:
        resource_id: The ID of the resource/deployment
        user_id: The ID of the user

    Returns:
        List of sessions or empty list if an error occurs
    """
    try:
        deployment = agent_engines.get(resource_id)
        sessions = deployment.list_sessions(user_id=user_id)
        print(f"Sessions for user {user_id}:")
        for session in sessions["sessions"]:
            print(f"- {session}")
        return list(
            sessions["sessions"]
        )  # Convert to list to ensure we have a concrete list
    except exceptions.GoogleAPIError as e:
        print(f"Error listing sessions: {e}")
        return []


def get_session(
    resource_id: str, user_id: str, session_id: str
) -> Optional[Dict[str, Any]]:
    """Gets a specific session for the specified user and resource.

    Args:
        resource_id: The ID of the resource/deployment
        user_id: The ID of the user
        session_id: The ID of the session

    Returns:
        The session information or None if an error occurs
    """
    try:
        deployment = agent_engines.get(resource_id)
        # Add a small delay to allow the session to be fully initialized
        time.sleep(1)
        session = deployment.get_session(user_id=user_id, session_id=session_id)
        print(f"Session {session_id} details: {session}")
        return session
    except exceptions.GoogleAPIError as e:
        print(f"Error getting session {session_id}: {e}")
        return None


def send_message(
    resource_id: str, user_id: str, session_id: str, message: str
) -> List[Any]:
    """Sends a message to a specific session.

    Args:
        resource_id: The ID of the resource/deployment
        user_id: The ID of the user
        session_id: The ID of the session
        message: The message to send

    Returns:
        The response events or empty list if an error occurs

    Note:
        If the agent doesn't respond to the message, no events will be printed.
        This is normal behavior if the agent is not configured to respond to messages.
    """
    try:
        deployment = agent_engines.get(resource_id)
        print(f"Sending message: '{message}'")
        events = []
        for event in deployment.stream_query(
            user_id=user_id,
            session_id=session_id,
            message=message,
        ):
            print(f"Response event: {event}")
            events.append(event)

        if not events:
            print("No response events received from the agent.")

        return events
    except exceptions.GoogleAPIError as e:
        print(f"Error sending message to session {session_id}: {e}")
        return []


def delete_session(resource_id: str, user_id: str, session_id: str) -> Optional[Any]:
    """Deletes a specific session.

    Args:
        resource_id: The ID of the resource/deployment
        user_id: The ID of the user
        session_id: The ID of the session

    Returns:
        The deletion response or None if an error occurs
    """
    try:
        deployment = agent_engines.get(resource_id)
        response = deployment.delete_session(user_id=user_id, session_id=session_id)
        print(f"Session {session_id} deleted: {response}")
        return response
    except exceptions.GoogleAPIError as e:
        print(f"Error deleting session {session_id}: {e}")
        return None


def delete_all_sessions(resource_id: str, user_id: str) -> int:
    """Deletes all sessions for a specific user.

    Args:
        resource_id: The ID of the resource/deployment
        user_id: The ID of the user

    Returns:
        Number of sessions deleted
    """
    try:
        deployment = agent_engines.get(resource_id)
        try:
            # Get sessions directly from the API to ensure we have the correct format
            sessions = deployment.list_sessions(user_id=user_id)
            print(f"Sessions for user {user_id}: {sessions}")
            sessions_list = list(sessions["sessions"])
            print(f"Sessions list: {sessions_list}")
            if not sessions_list:
                print(f"No sessions found for user {user_id}")
                return 0

            print(f"Deleting all {len(sessions_list)} sessions for user {user_id}...")
            deleted_count = 0

            # Try to get session IDs directly from the API response
            for session in sessions_list:
                try:
                    # Handle different possible formats of session objects
                    session_id = None
                    if isinstance(session, dict) and "id" in session:
                        session_id = session["id"]
                        print(f"Session ID is a dict: {session_id}")
                    elif isinstance(session, str):
                        # If session is a string, it might be the session ID itself
                        session_id = session
                        print(f"Session ID is a string: {session_id}")
                    elif hasattr(session, "id"):
                        # If session is an object with an id attribute
                        session_id = session.id
                        print(f"Session ID is an object: {session_id}")

                    if session_id:
                        delete_session(resource_id, user_id, session_id)
                        deleted_count += 1
                    else:
                        print(f"Could not determine session ID for: {session}")
                except Exception as e:
                    print(f"Error deleting session: {e}")
                    continue

            print(f"Deleted {deleted_count} sessions for user {user_id}")
            return deleted_count
        except Exception as e:
            print(f"Error listing sessions for deletion: {e}")
            return 0
    except exceptions.GoogleAPIError as e:
        print(f"Error accessing deployment for session deletion: {e}")
        return 0
