"""SQLite job store, token vault, settings and webhook unit tests."""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from nuguard.service.auth import Authenticator, generate_key, hash_key
from nuguard.service.models import ScanCreateRequest, ScanOptions
from nuguard.service.settings import (
    ApiKeyConfig,
    ServiceConfigError,
    ServiceSettings,
    load_service_settings,
)
from nuguard.service.store import JobRecord, new_job_id, utcnow
from nuguard.service.store.sqlite import SqliteJobStore
from nuguard.service.vault import TokenVault
from nuguard.service.webhooks import deliver, sign

# ── store ────────────────────────────────────────────────────────────────────


def _record(job_id: str, owner: str = "alpha", **kw) -> JobRecord:
    base = dict(id=job_id, owner_id=owner, status="queued", repo_url="https://github.com/a/b",
                request_json="{}", request_sha256="h", created_at=utcnow())
    base.update(kw)
    return JobRecord(**base)


def test_job_ids_are_ulids() -> None:
    ids = {new_job_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(i.startswith("scn_") and len(i) == 30 for i in ids)


def test_claim_is_conditional_and_finish_is_once(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "db" / "s.db")
    store.create(_record("scn_1"))
    assert store.claim("scn_1", utcnow())
    assert not store.claim("scn_1", utcnow())
    now = utcnow()
    assert store.finish("scn_1", status="succeeded", finished_at=now,
                        expires_at=now + timedelta(hours=1), result_json='{"a":1}',
                        result_sha256="x")
    assert not store.finish("scn_1", status="failed", finished_at=now, expires_at=now)
    assert store.get_result("scn_1") == ('{"a":1}', None)
    assert store.get("scn_1").status == "succeeded"


def test_large_results_go_to_private_files(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "db" / "s.db", inline_result_max_bytes=1024)
    store.create(_record("scn_big", status="running"))
    big = '{"x":"' + "a" * 5000 + '"}'
    now = utcnow()
    store.finish("scn_big", status="succeeded", finished_at=now, expires_at=now, result_json=big)
    path = Path(store.get("scn_big").result_ref)
    assert path.is_file() and (path.stat().st_mode & 0o777) == 0o600
    assert store.get_result("scn_big")[0] == big
    store.expire(now + timedelta(seconds=1))
    assert not path.exists()
    assert store.get("scn_big").status == "expired"


def test_idempotency_window(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "db" / "s.db")
    old = utcnow() - timedelta(hours=30)
    store.create(_record("scn_old", idempotency_key="k", created_at=old))
    assert store.find_idempotent("alpha", "k", utcnow() - timedelta(hours=24)) is None
    store.create(_record("scn_new", idempotency_key="k"))  # key freed for reuse
    assert store.find_idempotent("alpha", "k", utcnow() - timedelta(hours=24)).id == "scn_new"


def test_db_files_are_private(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "db" / "s.db")
    assert ((tmp_path / "db").stat().st_mode & 0o777) == 0o700
    assert ((tmp_path / "db" / "s.db").stat().st_mode & 0o777) == 0o600
    assert store.check_writable()


# ── vault / auth ─────────────────────────────────────────────────────────────


def test_vault_wipes_tokens() -> None:
    vault = TokenVault()
    vault.put("scn_1", SecretStr("secret"))
    assert "scn_1" in vault and vault.get("scn_1").get_secret_value() == "secret"
    vault.discard("scn_1")
    assert "scn_1" not in vault and len(vault) == 0


def test_authenticator() -> None:
    key = generate_key()
    auth = Authenticator([ApiKeyConfig(id="vf", sha256=hash_key(key))])
    assert auth.authenticate(f"Bearer {key}").id == "vf"
    assert auth.authenticate(f"bearer {key}").id == "vf"
    for bad in (None, "", "Bearer", f"Basic {key}", "Bearer wrong"):
        assert auth.authenticate(bad) is None


# ── settings ─────────────────────────────────────────────────────────────────


def _keys() -> list[ApiKeyConfig]:
    return [ApiKeyConfig(id="vf", sha256="a" * 64)]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "api_keys is empty"),
        ({"api_keys": _keys(), "git": {"allowed_hosts": []}}, "allowed_hosts is empty"),
        ({"api_keys": _keys(), "jobs": {"max_concurrent": 0}}, "max_concurrent"),
        ({"api_keys": _keys(), "bind": "0.0.0.0"}, "not loopback"),
        ({"api_keys": _keys(), "llm": {"enabled": True}}, "llm.model"),
    ],
)
def test_startup_refusals(kwargs, message) -> None:
    with pytest.raises(ServiceConfigError, match=message):
        ServiceSettings(**kwargs).validate_for_startup()


def test_public_bind_allowed_with_proxy_or_tls(tmp_path: Path) -> None:
    ServiceSettings(api_keys=_keys(), bind="0.0.0.0", trust_proxy=True).validate_for_startup()
    ServiceSettings(api_keys=_keys(), bind="0.0.0.0",
                    tls={"cert_file": tmp_path / "c", "key_file": tmp_path / "k"}
                    ).validate_for_startup()


def test_yaml_interpolation_and_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VF_SHA", "b" * 64)
    monkeypatch.setenv("NUGUARD_SERVICE_PORT", "9123")
    monkeypatch.setenv("NUGUARD_SERVICE_JOBS__MAX_CONCURRENT", "7")
    config = tmp_path / "nuguard.yaml"
    config.write_text(
        "service:\n  port: 8080\n  api_keys:\n    - id: vf\n      sha256: ${VF_SHA}\n"
        "  jobs:\n    max_concurrent: 2\n  llm:\n    model: ${UNSET_MODEL_VAR}\n"
    )
    settings = load_service_settings(config)
    assert settings.api_keys[0].sha256 == "b" * 64
    assert settings.port == 9123  # env beats file
    assert settings.jobs.max_concurrent == 7
    assert settings.llm.model is None  # unset ${VAR} → default


def test_passthrough_env_never_includes_credentials() -> None:
    settings = ServiceSettings(jobs={"passthrough_env": ["GITHUB_TOKEN", "LITELLM_API_KEY",
                                                         "NUGUARD_X", "HTTPS_PROXY"]})
    assert settings.jobs.passthrough_env == ["HTTPS_PROXY"]


def test_unknown_tool_in_defaults_rejected() -> None:
    with pytest.raises(ValidationError):
        ServiceSettings(analysis_defaults={"tools": ["nga-rules", "bogus"]})


# ── models ───────────────────────────────────────────────────────────────────


def test_request_never_serializes_token() -> None:
    token = "github_pat_" + "x" * 30
    request = ScanCreateRequest(repo_url="https://github.com/a/b", github_token=token)
    assert token not in request.model_dump_json() and token not in repr(request)


def test_tree_url_and_options_validation() -> None:
    ScanCreateRequest(repo_url="https://github.com/a/b/tree/main/svc/api")
    with pytest.raises(ValidationError):
        ScanOptions(subpath="/abs")
    with pytest.raises(ValidationError):
        ScanOptions(tools=[])
    with pytest.raises(ValidationError):
        ScanOptions(unexpected=True)  # type: ignore[call-arg]


# ── webhooks ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_signature_and_no_redirects(monkeypatch) -> None:
    from nuguard.service import webhooks
    from nuguard.service.settings import WebhookSettings

    monkeypatch.setattr(webhooks, "_resolves_public", lambda host, port: True)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302 if len(seen) == 1 else 204, headers={"Location": "https://x"})

    settings = WebhookSettings(allowed_hosts=["hooks.example.com"], secret=SecretStr("s3cret"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    body = b'{"id":"scn_1"}'
    ok = await deliver("https://hooks.example.com/cb", body, settings, client=client,
                       base_delay_s=0)
    assert ok and len(seen) == 2  # 302 is not followed; it is retried as a failure
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert seen[0].headers["X-NuGuard-Signature"] == expected == sign("s3cret", body)
    assert seen[0].headers["X-NuGuard-Timestamp"].isdigit()


@pytest.mark.asyncio
async def test_webhook_refuses_private_and_unlisted_hosts(monkeypatch) -> None:
    from nuguard.service import webhooks
    from nuguard.service.settings import WebhookSettings

    settings = WebhookSettings(allowed_hosts=["hooks.example.com", "localhost"],
                               secret=SecretStr("s"))
    assert not await deliver("https://evil.example.com/x", b"{}", settings)
    assert not webhooks._resolves_public("localhost", 443)
    assert not await deliver("https://localhost/x", b"{}", settings)
