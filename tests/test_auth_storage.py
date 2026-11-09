from __future__ import annotations

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lan_distribution_lab.__main__ import main
from lan_distribution_lab.app import create_app
from lan_distribution_lab.auth import COOKIE_NAME
from lan_distribution_lab.config import Settings
from lan_distribution_lab.errors import ApiError
from lan_distribution_lab.runtime import Runtime
from lan_distribution_lab.storage import LocalStateStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def auth_environment(tmp_path):
    clock = Clock()
    settings = Settings(
        data_dir=tmp_path / "state",
        managed_root=tmp_path / "managed",
        origin="http://testserver",
    )
    store = LocalStateStore(settings, now=clock.now)
    runtime = Runtime(settings, store=store)
    return settings, clock, runtime, TestClient(create_app(settings, runtime=runtime))


def _pair_cli(client: TestClient, code: str, *, label: str | None = None):
    body = {"pairing_code": code, "client_kind": "cli"}
    if label is not None:
        body["device_label"] = label
    return client.post("/api/pairings", json=body)


def _pair_browser(client: TestClient, code: str):
    return client.post(
        "/api/pairings",
        json={"pairing_code": code, "client_kind": "browser"},
        headers={"Origin": "http://testserver"},
    )


def test_valid_code_creates_cli_session(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    code = runtime.auth.create_pairing_code("read_write").code

    response = _pair_cli(client, code, label="test-cli")

    assert response.status_code == 201
    payload = response.json()
    assert payload["session"]["scope"] == "read_write"
    assert payload["access_token"].count(".") == 1
    assert runtime.auth.authenticate_token(payload["access_token"]).device_label == "test-cli"


def test_pairing_code_cannot_be_used_twice(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    code = runtime.auth.create_pairing_code("read").code

    assert _pair_cli(client, code).status_code == 201
    repeated = _pair_cli(client, code)

    assert repeated.status_code == 401
    assert repeated.json()["error"]["code"] == "authorization_required"


def test_expired_pairing_code_is_rejected(auth_environment) -> None:
    settings, clock, runtime, client = auth_environment
    code = runtime.auth.create_pairing_code("read").code
    clock.advance(settings.pairing_ttl_seconds + 1)

    response = _pair_cli(client, code)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authorization_required"


def test_concurrent_redemption_succeeds_only_once(auth_environment) -> None:
    _, _, runtime, _ = auth_environment
    code = runtime.auth.create_pairing_code("read").code

    def exchange() -> int:
        try:
            runtime.auth.exchange_pairing_code(
                pairing_code=code, client_kind="cli", device_label=None, source="concurrent-test"
            )
            return 201
        except ApiError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(lambda _: exchange(), range(8)))

    assert statuses.count(201) == 1
    assert all(status in {201, 401, 429} for status in statuses)
    assert sum(status in {401, 429} for status in statuses) == 7


def test_invalid_attempts_trigger_temporary_lockout(auth_environment) -> None:
    _, _, _, client = auth_environment

    for _ in range(5):
        response = _pair_cli(client, "111111")
        assert response.status_code == 401
    locked = _pair_cli(client, "111111")

    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "600"
    assert locked.json()["error"]["code"] == "rate_limited"


def test_browser_cookie_has_required_security_attributes(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    code = runtime.auth.create_pairing_code("read").code

    response = _pair_browser(client, code)

    assert response.status_code == 201
    assert "access_token" not in response.json()
    cookie = response.headers["set-cookie"]
    assert f"{COOKIE_NAME}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/api" in cookie
    assert "Secure" not in cookie


def test_read_and_read_write_scope_checks(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    read_token = _pair_cli(client, runtime.auth.create_pairing_code("read").code).json()["access_token"]
    write_token = _pair_cli(client, runtime.auth.create_pairing_code("read_write").code).json()["access_token"]

    assert runtime.auth.require_scope(read_token, "read").scope == "read"
    assert runtime.auth.require_scope(write_token, "read_write").scope == "read_write"
    with pytest.raises(ApiError, match="authorization_denied") as error:
        runtime.auth.require_scope(read_token, "read_write")
    assert error.value.status_code == 403


def test_bearer_credential_can_revoke_current_session(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    token = _pair_cli(client, runtime.auth.create_pairing_code("read").code).json()["access_token"]

    response = client.delete("/api/sessions/current", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 204
    with pytest.raises(ApiError, match="session_expired"):
        runtime.auth.authenticate_token(token)


def test_missing_or_invalid_credential_is_rejected(auth_environment) -> None:
    _, _, _, client = auth_environment

    missing = client.delete("/api/sessions/current")
    malformed = client.delete("/api/sessions/current", headers={"Authorization": "Basic ignored"})

    assert missing.status_code == malformed.status_code == 401
    assert missing.json()["error"]["code"] == "authorization_required"
    assert malformed.headers["www-authenticate"] == "Bearer"


def test_session_expiry_invalidates_token(auth_environment) -> None:
    settings, clock, runtime, client = auth_environment
    token = _pair_cli(client, runtime.auth.create_pairing_code("read").code).json()["access_token"]
    clock.advance(settings.session_ttl_seconds + 1)

    with pytest.raises(ApiError, match="session_expired") as error:
        runtime.auth.authenticate_token(token)
    assert error.value.status_code == 401


def test_cookie_revoke_requires_allowed_origin_and_clears_cookie(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    assert _pair_browser(client, runtime.auth.create_pairing_code("read").code).status_code == 201

    rejected = client.delete("/api/sessions/current", headers={"Origin": "https://evil.example"})
    accepted = client.delete("/api/sessions/current", headers={"Origin": "http://testserver"})

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "origin_not_allowed"
    assert accepted.status_code == 204
    assert "Max-Age=0" in accepted.headers["set-cookie"]


def test_browser_pairing_rejects_untrusted_origin(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    code = runtime.auth.create_pairing_code("read").code

    response = client.post(
        "/api/pairings",
        json={"pairing_code": code, "client_kind": "browser"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert _pair_cli(client, code).status_code == 201


def test_tokens_and_pairing_codes_are_not_logged_or_stored_in_plaintext(auth_environment, caplog) -> None:
    _, _, runtime, client = auth_environment
    caplog.set_level(logging.DEBUG)
    code = runtime.auth.create_pairing_code("read").code
    response = _pair_cli(client, code)
    token = response.json()["access_token"]

    with runtime.store.connection() as connection:
        pairing_digest = connection.execute("SELECT code_digest FROM pairing_codes").fetchone()[0]
        token_digest = connection.execute("SELECT token_digest FROM sessions").fetchone()[0]

    assert code.encode() not in pairing_digest
    assert token.encode() not in token_digest
    assert all(code not in record.getMessage() and token not in record.getMessage() for record in caplog.records)


def test_database_and_session_survive_runtime_restart(auth_environment) -> None:
    settings, clock, runtime, client = auth_environment
    token = _pair_cli(client, runtime.auth.create_pairing_code("read").code).json()["access_token"]

    restarted = Runtime(settings, store=LocalStateStore(settings, now=clock.now))

    assert restarted.auth.authenticate_token(token).scope == "read"


def test_layout_is_separate_and_contains_only_controlled_storage_dirs(auth_environment) -> None:
    settings, _, runtime, _ = auth_environment

    assert runtime.store.database_path.parent == settings.data_dir
    assert runtime.store.objects_dir == settings.managed_root / "objects"
    assert runtime.store.staging_dir == settings.managed_root / "staging"
    assert runtime.store.objects_dir.is_dir()
    assert runtime.store.staging_dir.is_dir()
    with pytest.raises(ValueError, match="separate"):
        LocalStateStore(Settings(data_dir=settings.data_dir, managed_root=settings.data_dir))
    with pytest.raises(ValueError, match="absolute"):
        LocalStateStore(Settings(data_dir=Path("relative-state"), managed_root=settings.managed_root))


def test_local_cli_pair_creation_uses_explicit_shared_data_directory(tmp_path, capsys) -> None:
    data_dir = tmp_path / "state"
    managed_root = tmp_path / "managed"

    main(
        [
            "pair",
            "create",
            "--scope",
            "read_write",
            "--data-dir",
            str(data_dir),
            "--managed-root",
            str(managed_root),
        ]
    )
    output = capsys.readouterr().out
    code = re.search(r"Pairing code: (\d{6})", output).group(1)
    settings = Settings(data_dir=data_dir, managed_root=managed_root)

    created = Runtime(settings).auth.exchange_pairing_code(
        pairing_code=code, client_kind="cli", device_label=None, source="same-host"
    )

    assert created.session.scope == "read_write"


def test_pairing_request_rejects_unsupported_input(auth_environment) -> None:
    _, _, _, client = auth_environment

    unsupported = client.post("/api/pairings", content="{}", headers={"Content-Type": "text/plain"})
    malformed = client.post("/api/pairings", json={"pairing_code": "123456", "client_kind": "cli", "extra": True})

    assert unsupported.status_code == 415
    assert malformed.status_code == 422


def test_database_contains_no_plaintext_access_token(auth_environment) -> None:
    _, _, runtime, client = auth_environment
    token = _pair_cli(client, runtime.auth.create_pairing_code("read").code).json()["access_token"]

    raw_database = runtime.store.database_path.read_bytes()

    assert token.encode("utf-8") not in raw_database
