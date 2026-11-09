"""Pairing, session authentication, scope checks, and FastAPI dependencies."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from fastapi import Depends, Request

from .errors import ApiError
from .models import AuthenticatedSession, CreatedSession, PairingCode, Scope, Session
from .storage import LocalStateStore

if TYPE_CHECKING:
    from .runtime import Runtime

COOKIE_NAME = "ldl_session"
PAIRING_CODE_PATTERN = re.compile(r"^\d{6}$")


class AuthService:
    """Keeps token handling and SQLite transactions outside route functions."""

    def __init__(self, store: LocalStateStore) -> None:
        self.store = store

    def create_pairing_code(self, scope: Scope) -> PairingCode:
        _validate_scope(scope)
        now = self.store.now_epoch()
        expires_at = now + self.store.settings.pairing_ttl_seconds
        for _ in range(20):
            code = f"{secrets.randbelow(1_000_000):06d}"
            digest = self._digest(code)
            try:
                with self.store.transaction() as connection:
                    connection.execute(
                        """
                        INSERT INTO pairing_codes (code_digest, scope, created_at, expires_at, used_at)
                        VALUES (?, ?, ?, ?, NULL)
                        """,
                        (digest, scope, now, expires_at),
                    )
                return PairingCode(code=code, scope=scope, expires_at=_to_datetime(expires_at))
            except sqlite3.IntegrityError:
                # A uniqueness collision is harmless; all other storage errors must be visible.
                continue
        raise RuntimeError("could not allocate a unique pairing code")

    def exchange_pairing_code(
        self,
        *,
        pairing_code: str,
        client_kind: Literal["browser", "cli"],
        device_label: str | None,
        source: str,
    ) -> CreatedSession:
        _validate_client_kind(client_kind)
        _validate_device_label(device_label)
        now = self.store.now_epoch()
        code_digest = self._digest(pairing_code)
        source_digest = self._digest(source)

        created: CreatedSession | None = None
        failure: ApiError | None = None
        with self.store.transaction() as connection:
            retry_after = self._retry_after(connection, "source", source_digest, now)
            retry_after = max(retry_after, self._retry_after(connection, "code", code_digest, now))
            if retry_after:
                raise ApiError(
                    429,
                    "rate_limited",
                    "Too many pairing attempts. Try again later.",
                    headers={"Retry-After": str(retry_after)},
                )

            code_row = connection.execute(
                """
                SELECT scope FROM pairing_codes
                WHERE code_digest = ? AND used_at IS NULL AND expires_at > ?
                """,
                (code_digest, now),
            ).fetchone()
            if code_row is None:
                self._register_failure(connection, "source", source_digest, now)
                self._register_failure(connection, "code", code_digest, now)
                failure = ApiError(401, "authorization_required", "Pairing code is invalid or expired.")
            else:
                consumed = connection.execute(
                    """
                    UPDATE pairing_codes SET used_at = ?
                    WHERE code_digest = ? AND used_at IS NULL AND expires_at > ?
                    """,
                    (now, code_digest, now),
                )
                if consumed.rowcount != 1:
                    self._register_failure(connection, "source", source_digest, now)
                    self._register_failure(connection, "code", code_digest, now)
                    failure = ApiError(401, "authorization_required", "Pairing code is invalid or expired.")
                else:
                    session_id = str(uuid.uuid4())
                    token_secret = secrets.token_urlsafe(32)
                    token = f"{session_id}.{token_secret}"
                    expires_at = now + self.store.settings.session_ttl_seconds
                    connection.execute(
                        """
                        INSERT INTO sessions (
                            session_id, token_digest, scope, created_at, expires_at, revoked_at, device_label
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                        """,
                        (
                            session_id,
                            self._digest(token_secret),
                            code_row["scope"],
                            now,
                            expires_at,
                            device_label,
                        ),
                    )
                    self._clear_failures(connection, source_digest, code_digest)
                    session = Session(
                        session_id=session_id,
                        scope=code_row["scope"],
                        created_at=_to_datetime(now),
                        expires_at=_to_datetime(expires_at),
                        revoked_at=None,
                        device_label=device_label,
                    )
                    created = CreatedSession(session=session, access_token=token)
        if failure is not None:
            raise failure
        assert created is not None
        return created

    def authenticate_token(self, token: str) -> Session:
        session_id, token_secret = _split_token(token)
        now = self.store.now_epoch()
        with self.store.connection() as connection:
            row = connection.execute(
                """
                SELECT session_id, token_digest, scope, created_at, expires_at, revoked_at, device_label
                FROM sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None or not hmac.compare_digest(row["token_digest"], self._digest(token_secret)):
            raise ApiError(
                401,
                "authorization_required",
                "A valid session is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if row["revoked_at"] is not None:
            raise ApiError(
                401,
                "session_expired",
                "The session is no longer valid.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if row["expires_at"] <= now:
            raise ApiError(
                401,
                "session_expired",
                "The session is no longer valid.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return _session_from_row(row)

    def revoke_session(self, session_id: str) -> None:
        now = self.store.now_epoch()
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (now, session_id),
            )

    def require_scope(self, token: str, required_scope: Scope) -> Session:
        session = self.authenticate_token(token)
        if required_scope == "read_write" and session.scope != "read_write":
            raise ApiError(403, "authorization_denied", "The session does not have write permission.")
        return session

    def _digest(self, value: str) -> bytes:
        return hmac.new(
            self.store.secret_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def _retry_after(self, connection, key_type: str, key_value: bytes, now: int) -> int:
        row = connection.execute(
            """
            SELECT window_started_at, locked_until FROM pairing_failures
            WHERE key_type = ? AND key_value = ?
            """,
            (key_type, key_value),
        ).fetchone()
        if row is None:
            return 0
        if row["locked_until"] is not None and row["locked_until"] > now:
            return row["locked_until"] - now
        return 0

    def _register_failure(self, connection, key_type: str, key_value: bytes, now: int) -> None:
        row = connection.execute(
            """
            SELECT window_started_at, failure_count FROM pairing_failures
            WHERE key_type = ? AND key_value = ?
            """,
            (key_type, key_value),
        ).fetchone()
        if row is None or now - row["window_started_at"] >= self.store.settings.pair_failure_window_seconds:
            window_started_at = now
            count = 1
        else:
            window_started_at = row["window_started_at"]
            count = row["failure_count"] + 1
        locked_until = (
            now + self.store.settings.pair_lockout_seconds
            if count >= self.store.settings.max_pair_failures
            else None
        )
        connection.execute(
            """
            INSERT INTO pairing_failures (
                key_type, key_value, window_started_at, failure_count, locked_until
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key_type, key_value) DO UPDATE SET
                window_started_at = excluded.window_started_at,
                failure_count = excluded.failure_count,
                locked_until = excluded.locked_until
            """,
            (key_type, key_value, window_started_at, count, locked_until),
        )

    def _clear_failures(self, connection, source_digest: bytes, code_digest: bytes) -> None:
        connection.execute(
            "DELETE FROM pairing_failures WHERE key_type = 'source' AND key_value = ?",
            (source_digest,),
        )
        connection.execute(
            "DELETE FROM pairing_failures WHERE key_type = 'code' AND key_value = ?",
            (code_digest,),
        )


def get_runtime(request: Request) -> "Runtime":
    return request.app.state.runtime_provider.get()


def get_current_session(
    request: Request, runtime=Depends(get_runtime)
) -> AuthenticatedSession:
    authorization = request.headers.get("Authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or token.strip() != token:
            raise ApiError(
                401,
                "authorization_required",
                "A valid session is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return AuthenticatedSession(runtime.auth.authenticate_token(token), "bearer")

    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise ApiError(
            401,
            "authorization_required",
            "A valid session is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthenticatedSession(runtime.auth.authenticate_token(token), "cookie")


def require_read(
    authenticated: AuthenticatedSession = Depends(get_current_session),
) -> AuthenticatedSession:
    return authenticated


def require_read_write(
    authenticated: AuthenticatedSession = Depends(get_current_session),
) -> AuthenticatedSession:
    if authenticated.session.scope != "read_write":
        raise ApiError(403, "authorization_denied", "The session does not have write permission.")
    return authenticated


def require_allowed_origin(request: Request, runtime: "Runtime") -> None:
    if request.headers.get("Origin") != runtime.settings.origin:
        raise ApiError(403, "origin_not_allowed", "The request origin is not allowed.")


def _split_token(token: str) -> tuple[str, str]:
    session_id, separator, token_secret = token.partition(".")
    if not separator or not token_secret:
        raise ApiError(
            401,
            "authorization_required",
            "A valid session is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        uuid.UUID(session_id)
    except ValueError as error:
        raise ApiError(
            401,
            "authorization_required",
            "A valid session is required.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    return session_id, token_secret


def _validate_scope(scope: str) -> None:
    if scope not in {"read", "read_write"}:
        raise ValueError("scope must be read or read_write")


def _validate_client_kind(client_kind: str) -> None:
    if client_kind not in {"browser", "cli"}:
        raise ApiError(422, "invalid_input", "client_kind must be browser or cli.")


def _validate_device_label(device_label: str | None) -> None:
    if device_label is None:
        return
    if not 1 <= len(device_label) <= 64 or any(character.isspace() and character != " " for character in device_label):
        raise ApiError(422, "invalid_input", "device_label is invalid.")


def _session_from_row(row) -> Session:
    revoked_at = row["revoked_at"]
    return Session(
        session_id=row["session_id"],
        scope=row["scope"],
        created_at=_to_datetime(row["created_at"]),
        expires_at=_to_datetime(row["expires_at"]),
        revoked_at=_to_datetime(revoked_at) if revoked_at is not None else None,
        device_label=row["device_label"],
    )


def _to_datetime(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)
