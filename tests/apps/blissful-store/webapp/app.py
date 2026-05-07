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
import urllib.parse
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
PROFILE_MANIFEST_PATH = BASE_DIR.parent / "customer_profiles.json"
CUSTOMER_RECORDS_PATH = BASE_DIR.parent / "mock_crm" / "customer_records.json"
if load_dotenv and ENV_PATH.exists():
    load_dotenv(ENV_PATH)

PROJECT_ID = os.getenv("BLISSFUL_PROJECT_ID", "platform-dev-2025")
LOCATION = os.getenv("BLISSFUL_LOCATION", "us")
APP_ID = os.getenv("BLISSFUL_APP_ID", "dfe2a521-59d6-459a-8358-cedc73f1a92e")
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


def _load_profile_manifest() -> tuple[str, dict[str, dict[str, str]]]:
    with PROFILE_MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    if not isinstance(manifest, dict):
        raise RuntimeError("customer_profiles.json must contain a top-level object")

    default_profile = manifest.get("default_profile")
    profiles = manifest.get("profiles")
    if not isinstance(default_profile, str) or not default_profile:
        raise RuntimeError("customer_profiles.json must define a non-empty default_profile")
    if not isinstance(profiles, dict):
        raise RuntimeError("customer_profiles.json must define a 'profiles' object")

    normalized_profiles: dict[str, dict[str, str]] = {}
    for key, value in profiles.items():
        if not isinstance(key, str) or not key:
            raise RuntimeError("customer_profiles.json profile keys must be non-empty strings")
        if not isinstance(value, dict):
            raise RuntimeError(f"customer_profiles.json entry '{key}' must be an object")

        label = value.get("label")
        if not isinstance(label, str) or not label:
            raise RuntimeError(f"customer_profiles.json entry '{key}' must define a non-empty label")

        normalized_profiles[key] = {"label": label}

    if default_profile not in normalized_profiles:
        raise RuntimeError("customer_profiles.json default_profile must exist in profiles")

    return default_profile, normalized_profiles


def _load_customer_records() -> dict[str, dict]:
    with CUSTOMER_RECORDS_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    customers = payload.get("customers")
    if not isinstance(customers, list):
        raise RuntimeError("customer_records.json must contain a 'customers' list")

    records_by_profile: dict[str, dict] = {}
    for customer in customers:
        if not isinstance(customer, dict):
            raise RuntimeError("customer_records.json customers entries must be objects")

        profile_key = customer.get("profile_key")
        if not isinstance(profile_key, str) or not profile_key:
            raise RuntimeError("Each customer record must define a non-empty profile_key")

        records_by_profile[profile_key] = customer

    return records_by_profile


def _first_billing_address(customer_record: dict) -> dict:
    addresses = customer_record.get("addresses")
    if not isinstance(addresses, list):
        return {}

    for address in addresses:
        if not isinstance(address, dict):
            continue
        if address.get("type") == "billing":
            return {
                "street": address.get("street", ""),
                "city": address.get("city", ""),
                "state": address.get("state", ""),
                "zip": address.get("zip", ""),
            }

    return {}


def _build_session_profile(profile_key: str, customer_record: dict) -> dict:
    contact = customer_record.get("contact") or {}
    metrics = customer_record.get("metrics") or {}
    preferences = customer_record.get("preferences") or {}
    communication_preferences = preferences.get("communication") or {}
    name = customer_record.get("name") or {}
    domain_context = customer_record.get("domain_context") or {}
    blissful_store_context = domain_context.get("blissful_store") or {}

    return {
        "profile_key": profile_key,
        "account_number": (customer_record.get("external_ids") or {}).get("account_number", ""),
        "customer_id": customer_record.get("customer_id", ""),
        "billing_address": _first_billing_address(customer_record),
        "communication_preferences": communication_preferences,
        "customer_first_name": name.get("first_name", ""),
        "customer_last_name": name.get("last_name", ""),
        "customer_start_date": metrics.get("customer_since", ""),
        "email": contact.get("email", ""),
        "garden_profile": blissful_store_context.get("garden_profile", {}),
        "loyalty_points": metrics.get("loyalty_points", 0),
        "phone_number": contact.get("phone", ""),
        "preferred_store": preferences.get("preferred_location", ""),
        "purchase_history": customer_record.get("purchase_history", []),
        "scheduled_appointments": customer_record.get("appointments", {}),
        "years_as_customer": metrics.get("tenure_years", 0),
    }


PROFILE_DEFAULT, PROFILE_MANIFEST = _load_profile_manifest()
CUSTOMER_RECORDS = _load_customer_records()

missing_manifest_profiles = [
    profile_key for profile_key in PROFILE_MANIFEST if profile_key not in CUSTOMER_RECORDS
]
if missing_manifest_profiles:
    raise RuntimeError(
        "customer_profiles.json contains unknown profiles: "
        + ", ".join(sorted(missing_manifest_profiles))
    )

missing_manifest_labels = [
    profile_key for profile_key in CUSTOMER_RECORDS if profile_key not in PROFILE_MANIFEST
]
if missing_manifest_labels:
    raise RuntimeError(
        "customer_profiles.json is missing labels for CRM profiles: "
        + ", ".join(sorted(missing_manifest_labels))
    )


PROFILE_OPTIONS = [
    {"key": profile_key, "label": metadata["label"]}
    for profile_key, metadata in PROFILE_MANIFEST.items()
]


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
        str(Path(__file__).resolve().parents[4] / "tmp" / "google-cloud-sdk" / "bin" / "gcloud"),
    ]
    gcloud_commands = [
        ["auth", "print-access-token"],
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
    sanitized_outputs: list[dict] = []
    seen_texts: set[str] = set()
    for item in outputs:
        if not isinstance(item, dict):
            sanitized_outputs.append(item)
            continue
        text = item.get("text")
        if not isinstance(text, str):
            sanitized_outputs.append(item)
            continue

        # CES fixture fallback currently contains verbose platform copy and rich formatting.
        if (
            "Thanks for trying out" in text
            or "CX Agent Studio" in text
            or "Now that we've clarified that" in text
        ):
            item["text"] = FALLBACK_PLAIN_MESSAGE
            updated = True

        normalized_text = item.get("text")
        if isinstance(normalized_text, str):
            if normalized_text in seen_texts:
                updated = True
                continue
            seen_texts.add(normalized_text)

        sanitized_outputs.append(item)

    if not updated:
        return response_body

    payload["outputs"] = sanitized_outputs
    return json.dumps(payload).encode("utf-8")


def _normalize_profile_name(profile_name: str | None) -> str:
    if not profile_name:
        return PROFILE_DEFAULT

    normalized = str(profile_name).strip()
    return normalized or PROFILE_DEFAULT


def _get_customer_profile(profile_name: str | None) -> tuple[str, dict]:
    selected_name = _normalize_profile_name(profile_name)
    selected_record = CUSTOMER_RECORDS.get(selected_name)
    if selected_record is None:
        available_profiles = ", ".join(sorted(CUSTOMER_RECORDS))
        raise ValueError(
            f"Unknown profile '{selected_name}'. Available profiles: {available_profiles}"
        )

    return selected_name, _build_session_profile(selected_name, selected_record)


def _get_requested_profile_name(handler: SimpleHTTPRequestHandler, request_json: dict) -> str:
    body_profile = request_json.get("profile")
    if body_profile is not None:
        return _normalize_profile_name(str(body_profile))

    parsed_url = urllib.parse.urlparse(handler.path)
    query_params = urllib.parse.parse_qs(parsed_url.query)
    query_profile = query_params.get("profile", [None])[0]
    if query_profile is not None:
        return _normalize_profile_name(query_profile)

    env_profile = os.getenv("BLISSFUL_CUSTOMER_PROFILE")
    return _normalize_profile_name(env_profile)


class ChatProxyHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path == "/api/profiles":
            self._send_json(
                200,
                {
                    "default_profile": PROFILE_DEFAULT,
                    "profiles": PROFILE_OPTIONS,
                },
            )
            return
        if parsed_path.path in {"", "/"}:
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
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path != "/api/chat":
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

        try:
            profile_name = _get_requested_profile_name(self, request_json)
            _, customer_profile = _get_customer_profile(profile_name)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        payload = {
            "config": {
                "session": session_name,
                "deployment": (
                    f"projects/{PROJECT_ID}/locations/{LOCATION}/apps/{APP_ID}"
                    f"/deployments/{DEPLOYMENT_ID}"
                ),
            },
            "inputs": [
                {"variables": {"customer_profile": customer_profile}},
                {"text": user_text},
            ],
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
