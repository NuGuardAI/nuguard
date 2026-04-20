import os
import re
import subprocess
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:5001"
OUT = "/Users/ganesh/work/nuguard/nuguard-private/tests/apps/stock-assist/reports/redteam.with-llm.json"


def extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("csrf token not found")
    return match.group(1)


session = requests.Session()
root = session.get(f"{BASE}/", timeout=20)
print(f"ROOT_STATUS={root.status_code}")

email = f"copilot.redteam.llm.{int(time.time())}@example.com"
password = "Password123!"

reg_page = session.get(f"{BASE}/auth/register", timeout=20)
reg_page.raise_for_status()
reg_csrf = extract_csrf(reg_page.text)
session.post(
    f"{BASE}/auth/register",
    data={
        "csrf_token": reg_csrf,
        "name": "Copilot Redteam LLM",
        "email": email,
        "password": password,
        "confirm": password,
        "agree_tos": "y",
    },
    timeout=30,
)

login_page = session.get(f"{BASE}/auth/login", timeout=20)
login_page.raise_for_status()
login_csrf = extract_csrf(login_page.text)
session.post(
    f"{BASE}/auth/login",
    data={
        "csrf_token": login_csrf,
        "email": email,
        "password": password,
    },
    timeout=30,
)

cookie = session.cookies.get("session")
if not cookie:
    raise RuntimeError("session cookie missing")
print(f"SESSION_COOKIE_LENGTH={len(cookie)}")
print(f"LITELLM_API_KEY_SET={bool(os.environ.get('LITELLM_API_KEY'))}")

env = os.environ.copy()
env["STOCK_ASSIST_SESSION_COOKIE"] = cookie

cmd = [
    "/Users/ganesh/work/nuguard/nuguard-private/penv/bin/python",
    "-m",
    "nuguard.cli.main",
    "redteam",
    "--config",
    "tests/apps/stock-assist/nuguard.yaml",
    "--output",
    "tests/apps/stock-assist/reports/redteam.with-llm.json",
]
run = subprocess.run(cmd, cwd="/Users/ganesh/work/nuguard/nuguard-private", env=env)
print(f"REDTEAM_RC={run.returncode}")
out = Path(OUT)
print(f"REPORT_EXISTS={out.exists()}")
