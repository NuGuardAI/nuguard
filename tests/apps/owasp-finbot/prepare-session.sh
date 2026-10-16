#!/usr/bin/env bash
# Source before NuGuard runs: obtain a temporary session with CSRF protection enabled.
# Credentials stay in the process environment and are never written to disk.
prepare_finbot_session() {
  local session_data
  session_data="$(uv run python - "$SCRIPT_DIR/nuguard.yaml" <<'PY'
import os
import sys
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import yaml


class CSRFParser(HTMLParser):
    token = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "csrf-token":
            self.token = attributes.get("content", "")


try:
    with open(sys.argv[1], encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    base_url = os.path.expandvars(
        os.environ.get("FINBOT_SESSION_URL") or config["target"]["url"]
    ).rstrip("/")
    cookies = CookieJar()
    client = build_opener(HTTPCookieProcessor(cookies))
    # Match target.headers so FinBot's session fingerprint stays consistent.
    headers = {
        "User-Agent": "NuGuard-FinBot/1.0",
        "Accept-Language": "en-US",
        "Accept-Encoding": "identity",
    }
    with client.open(Request(base_url + "/vendor/", headers=headers), timeout=30) as response:
        parser = CSRFParser()
        parser.feed(response.read().decode("utf-8"))
    cookie_header = "; ".join(
        f"{cookie.name}={cookie.value}" for cookie in cookies
        if cookie.name == "finbot_session"
    )
    if not cookie_header or not parser.token:
        raise ValueError("Missing FinBot session cookie or CSRF meta token")
    # An invalid body must reach schema validation without running the AI.
    # FinBot's mounted vendor app renders schema failures as HTTP 400;
    # versions recognizing the mounted path as an API return 422 JSON instead.
    probe = Request(
        base_url + "/vendor/api/v1/chat",
        data=b"{}",
        headers={**headers, "Cookie": cookie_header,
                 "X-CSRF-Token": parser.token, "Content-Type": "application/json"},
    )
    try:
        client.open(probe, timeout=30).close()
    except HTTPError as error:
        if error.code not in (400, 422):
            raise ValueError(f"FinBot session validation returned HTTP {error.code}") from None
    else:
        raise ValueError("FinBot chat endpoint did not validate its required message field")
    values = (cookie_header, parser.token)
    if any(any(char in value for char in "\r\n\t") for value in values):
        raise ValueError("Unexpected control character in session credentials")
    print("\t".join(values))
except (HTTPError, URLError, OSError, ValueError, KeyError) as error:
    # Do not include response bodies, cookies, or token values in diagnostics.
    message = str(error) if isinstance(error, ValueError) else type(error).__name__
    sys.exit(f"FinBot session preparation failed: {message}")
PY
)" || return 1
  IFS=$'\t' read -r FINBOT_SESSION_COOKIE FINBOT_CSRF_TOKEN <<< "$session_data"
  export FINBOT_SESSION_COOKIE FINBOT_CSRF_TOKEN
  echo "FinBot temporary session and CSRF token verified."
}
prepare_finbot_session || return $?
unset -f prepare_finbot_session
