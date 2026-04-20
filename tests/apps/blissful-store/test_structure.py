import json
import subprocess
import urllib.error
import urllib.request
import uuid


def main() -> None:
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()

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

    try:
        with urllib.request.urlopen(req) as response:
            resp_body = response.read().decode("utf-8")
            resp_json = json.loads(resp_body)
            with open("response.json", "w") as f:
                json.dump(resp_json, f, indent=2)
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
        print(e.read().decode("utf-8"))


if __name__ == "__main__":
    main()
