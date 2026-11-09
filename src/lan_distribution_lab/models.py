"""Small domain objects shared by authentication and HTTP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Scope = Literal["read", "read_write"]


@dataclass(frozen=True, slots=True)
class PairingCode:
    code: str
    scope: Scope
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    scope: Scope
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    device_label: str | None


@dataclass(frozen=True, slots=True)
class CreatedSession:
    session: Session
    access_token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    session: Session
    credential_source: Literal["bearer", "cookie"]


@dataclass(frozen=True, slots=True)
class FileRecord:
    file_id: str
    display_name: str
    storage_key: str
    size_bytes: int
    content_sha256: str
    etag: str
    created_at: datetime
    publisher_session_id: str


@dataclass(frozen=True, slots=True)
class TransferRecord:
    transfer_id: str
    session_id: str
    file_id: str | None
    direction: Literal["upload", "download"]
    state: Literal["started", "completed", "failed", "cancelled"]
    bytes_transferred: int
    size_bytes: int | None
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None

