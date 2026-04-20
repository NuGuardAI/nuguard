import json
import os
import subprocess
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error
import urllib.request

try:
    import google.auth
    import google.auth.transport.requests
    _GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    _GOOGLE_AUTH_AVAILABLE = False

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / ".env"
if load_dotenv and ENV_PATH.exists():
    load_dotenv(ENV_PATH)

PROJECT_ID = os.getenv("BLISSFUL_PROJECT_ID", "platform-dev-2025")
LOCATION = os.getenv("BLISSFUL_LOCATION", "us")
APP_ID = os.getenv("BLISSFUL_APP_ID", "dfe2a521-59d6-459a-8358-cedc73f1a92e")
APP_VERSION = os.getenv(
    "BLISSFUL_APP_VERSION",
    "5317c9ed-d32c-4c34-9f5a-db08cb1f4bb1",
)
DEPLOYMENT_ID = os.getenv(
    "BLISSFUL_DEPLOYMENT_ID",
    "95a08aa7-8453-4439-8218-a9ba77dfdf47",
)
SERVER_PORT = int(os.getenv("PORT", "8081"))
SERVER_HOST = os.getenv("HOST", "127.0.0.1")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("BLISSFUL_REQUEST_TIMEOUT", "60"))

FALLBACK_PLAIN_MESSAGE = (
    "I can only help with Blissful Home and Garden shopping topics. "
    "I can help identify plants, share care tips, recommend products, "
    "and help with cart updates."
)

TOKEN_REFRESH_BUFFER_SECONDS = 120
TOKEN_DEFAULT_LIFETIME_SECONDS = 3300
_TOKEN_LOCK = threading.Lock()
_CACHED_ACCESS_TOKEN: str | None = None
_CACHED_ACCESS_TOKEN_EXPIRY_TS: float = 0.0
_ADC_CREDENTIALS = None


def _clear_cached_auth_state() -> None:
    global _CACHED_ACCESS_TOKEN, _CACHED_ACCESS_TOKEN_EXPIRY_TS, _ADC_CREDENTIALS
    with _TOKEN_LOCK:
        _CACHED_ACCESS_TOKEN = None
        _CACHED_ACCESS_TOKEN_EXPIRY_TS = 0.0
        _ADC_CREDENTIALS = None


def build_session_name(session_id: str) -> str:
    return (
        f"projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}/sessions/{session_id}"
    )


def build_api_url(session_id: str) -> str:
    return (
        "https://ces.googleapis.com/v1beta/"
        f"{build_session_name(session_id)}:runSession"
    )


def _cache_access_token(token: str, expiry_ts: float | None = None) -> str:
    global _CACHED_ACCESS_TOKEN, _CACHED_ACCESS_TOKEN_EXPIRY_TS
    _CACHED_ACCESS_TOKEN = token
    if expiry_ts is None:
        _CACHED_ACCESS_TOKEN_EXPIRY_TS = time.time() + TOKEN_DEFAULT_LIFETIME_SECONDS
    else:
        _CACHED_ACCESS_TOKEN_EXPIRY_TS = expiry_ts
    return token


def _cached_token_is_valid() -> bool:
    return (
        bool(_CACHED_ACCESS_TOKEN)
        and _CACHED_ACCESS_TOKEN_EXPIRY_TS > (time.time() + TOKEN_REFRESH_BUFFER_SECONDS)
    )


def _get_token_from_adc() -> str | None:
    global _ADC_CREDENTIALS
    if not _GOOGLE_AUTH_AVAILABLE:
        return None

    try:
        if _ADC_CREDENTIALS is None:
            _ADC_CREDENTIALS, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )

        req = google.auth.transport.requests.Request()
        _ADC_CREDENTIALS.refresh(req)
        if not _ADC_CREDENTIALS.token:
            return None

        expiry_ts = None
        if getattr(_ADC_CREDENTIALS, "expiry", None) is not None:
            expiry_ts = _ADC_CREDENTIALS.expiry.timestamp()
        return _cache_access_token(_ADC_CREDENTIALS.token, expiry_ts=expiry_ts)
    except Exception:
        return None


def _get_token_from_gcloud() -> str | None:
    gcloud_candidates = [
        "gcloud",
        str(Path(__file__).resolve().parents[3] / "tmp" / "google-cloud-sdk" / "bin" / "gcloud"),
    ]
    gcloud_commands = [
        ["auth", "application-default", "print-access-token"],
    ]

    for gcloud_bin in gcloud_candidates:
        for command in gcloud_commands:
            try:
                token = subprocess.check_output(
                    [gcloud_bin, *command],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                ).strip()
                if token:
                    return _cache_access_token(token)
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue

    return None


def get_access_token() -> str:
    # Fast path for repeated requests in threaded runs.
    if _cached_token_is_valid():
        return _CACHED_ACCESS_TOKEN or ""

    with _TOKEN_LOCK:
        if _cached_token_is_valid():
            return _CACHED_ACCESS_TOKEN or ""

        # 1. Explicit env token (must be a real OAuth2 access token, not an API key)
        env_token = os.getenv("GCP_ACCESS_TOKEN") or os.getenv("GOOGLE_ACCESS_TOKEN")
        if env_token:
            return _cache_access_token(env_token.strip())

        # 2. Application Default Credentials via google-auth (best in dev containers)
        adc_token = _get_token_from_adc()
        if adc_token:
            return adc_token

        # 3. gcloud CLI fallback
        gcloud_token = _get_token_from_gcloud()
        if gcloud_token:
            return gcloud_token

    raise RuntimeError(
        "No GCP OAuth2 access token available.\n"
        "  Option A: run 'gcloud auth application-default login'\n"
        "  Option B: set GCP_ACCESS_TOKEN=<token> in .env (tokens expire in ~1h)\n"
        "  Note: GOOGLE_CLOUD_API_KEY is an API key, not an OAuth2 token — it won't work here."
    )


def _sanitize_ces_response(response_body: bytes) -> bytes:
    try:
        payload = json.loads(response_body)
    except Exception:
        return response_body

    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return response_body

    updated = False
    for item in outputs:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue

        # CES fixture fallback currently contains verbose platform copy and rich formatting.
        if (
            "Thanks for trying out" in text
            or "CX Agent Studio" in text
            or "Now that we've clarified that" in text
        ):
            item["text"] = FALLBACK_PLAIN_MESSAGE
            updated = True

    if not updated:
        return response_body

    return json.dumps(payload).encode("utf-8")


class ChatProxyHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"", "/"}:
            self.path = "/index.html"
        super().do_GET()

    def _send_json(self, status_code: int, body: dict | list) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self._send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            request_body = self.rfile.read(content_length)
            request_json = json.loads(request_body or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "Request body must be valid JSON"})
            return

        user_text = str(request_json.get("text", "")).strip() or "hi"
        session_id = request_json.get("session_id") or f"test_session_{uuid.uuid4().hex[:8]}"
        session_name = build_session_name(session_id)

        payload = {
            "config": {
                "session": session_name,
                "app_version": (
                    f"projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}"
                    f"/versions/{APP_VERSION}"
                ),
                "deployment": (
                    f"projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}"
                    f"/deployments/{DEPLOYMENT_ID}"
                ),
            },
            "inputs": [{"text": user_text}],
        }

        try:
            for attempt in range(2):
                try:
                    token = get_access_token()
                    upstream_request = urllib.request.Request(
                        build_api_url(session_id),
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )

                    print(f"Forwarding request for session {session_id}")
                    with urllib.request.urlopen(
                        upstream_request,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    ) as response:
                        response_body = _sanitize_ces_response(response.read())
                        self.send_response(response.status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(response_body)))
                        self.end_headers()
                        self.wfile.write(response_body)
                        return
                except urllib.error.HTTPError as exc:
                    error_body = exc.read()
                    # Recover once when CES rejects token type (stale/unsupported token).
                    if (
                        attempt == 0
                        and exc.code == 401
                        and b"ACCESS_TOKEN_TYPE_UNSUPPORTED" in error_body
                    ):
                        print("CES token type unsupported; refreshing token and retrying once")
                        _clear_cached_auth_state()
                        continue

                    print(f"CES returned HTTP {exc.code}: {error_body.decode('utf-8', errors='replace')}")
                    self.send_response(exc.code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(error_body)))
                    self.end_headers()
                    self.wfile.write(error_body)
                    return
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


if __name__ == "__main__":
    handler = partial(ChatProxyHandler, directory=str(BASE_DIR))
    server_address = (SERVER_HOST, SERVER_PORT)
    try:
        httpd = ThreadingHTTPServer(server_address, handler)
    except OSError as exc:
        if "Address already in use" in str(exc):
            print(
                f"Port {SERVER_PORT} is already in use. "
                f"Stop the existing process first:\n"
                f"  fuser -k {SERVER_PORT}/tcp"
            )
            raise SystemExit(1) from None
        raise
    print(f"Serving Blissful webapp on http://{SERVER_HOST}:{SERVER_PORT}")
    httpd.serve_forever()
