import json
import urllib.error
import urllib.request
import uuid

import google.auth
import google.auth.transport.requests


def get_access_token() -> str:
    """Return an OAuth2 access token from Application Default Credentials."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    if not credentials.token:
        raise RuntimeError("ADC did not return an access token")
    return credentials.token

def main() -> None:
    token = get_access_token()

    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    url = f"https://ces.googleapis.com/v1beta/projects/platform-dev-2025/locations/us/apps/dfe2a521-59d6-459a-8358-cedc73f1a92e/sessions/{session_id}:runSession"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "config": {
            "session": f"projects/platform-dev-2025/locations/us/apps/dfe2a521-59d6-459a-8358-cedc73f1a92e/sessions/{session_id}",
            "app_version": "projects/platform-dev-2025/locations/us/apps/dfe2a521-59d6-459a-8358-cedc73f1a92e/versions/5317c9ed-d32c-4c34-9f5a-db08cb1f4bb1",
            "deployment": "projects/platform-dev-2025/locations/us/apps/dfe2a521-59d6-459a-8358-cedc73f1a92e/deployments/95a08aa7-8453-4439-8218-a9ba77dfdf47",
        },
        "inputs": [{"text": "what can you help with"}],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    print("Invoking API...")
    try:
        with urllib.request.urlopen(req) as response:
            print("Status Code:", response.status)
            resp_body = response.read().decode("utf-8")
            try:
                print("Response JSON:", json.dumps(json.loads(resp_body), indent=2))
            except json.JSONDecodeError:
                print("Response Text:", resp_body)
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
        print("Response Text:", e.read().decode("utf-8"))
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
