from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_log = logging.getLogger("toolbox.http")


def get_json(url: str, params: dict[str, str], headers: dict[str, str], timeout: float, retries: int) -> dict[str, Any]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _log.debug("GET %s (attempt %d/%d)", url, attempt + 1, retries + 1)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                _log.debug("GET %s → HTTP %s", url, response.status)
                if not body:
                    return {"result": []}
                result: dict[str, Any] = json.loads(body)
                return result
        except urllib.error.HTTPError as exc:
            _log.warning("GET %s → HTTP %s %s (attempt %d)", url, exc.code, exc.reason, attempt + 1)
            last_error = exc
        except urllib.error.URLError as exc:
            _log.warning("GET %s → URL error: %s (attempt %d)", url, exc.reason, attempt + 1)
            last_error = exc
        except (TimeoutError, json.JSONDecodeError) as exc:
            _log.warning("GET %s → %s: %s (attempt %d)", url, type(exc).__name__, exc, attempt + 1)
            last_error = exc

        if attempt < retries:
            delay = min(2 ** attempt, 5)
            _log.debug("retrying in %ds …", delay)
            time.sleep(delay)

    if last_error is None:
        raise RuntimeError("request failed")
    raise RuntimeError(f"request failed after {retries + 1} attempt(s): {last_error}")


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float, retries: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _log.debug("POST %s (attempt %d/%d)", url, attempt + 1, retries + 1)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                _log.debug("POST %s → HTTP %s", url, response.status)
                if not body:
                    return {"status": response.status}
                result: dict[str, Any] = json.loads(body)
                return result
        except urllib.error.HTTPError as exc:
            _log.warning("POST %s → HTTP %s %s (attempt %d)", url, exc.code, exc.reason, attempt + 1)
            last_error = exc
        except urllib.error.URLError as exc:
            _log.warning("POST %s → URL error: %s (attempt %d)", url, exc.reason, attempt + 1)
            last_error = exc
        except (TimeoutError, json.JSONDecodeError) as exc:
            _log.warning("POST %s → %s: %s (attempt %d)", url, type(exc).__name__, exc, attempt + 1)
            last_error = exc

        if attempt < retries:
            delay = min(2 ** attempt, 5)
            _log.debug("retrying in %ds …", delay)
            time.sleep(delay)

    if last_error is None:
        raise RuntimeError("request failed")
    raise RuntimeError(f"request failed after {retries + 1} attempt(s): {last_error}")
