"""Local-only configuration for service state and managed storage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_state_dir() -> Path:
    if os.name == "nt" and (local_app_data := os.environ.get("LOCALAPPDATA")):
        return Path(local_app_data) / "LANDistributionLab" / "state"
    return Path.home() / ".local" / "share" / "lan-distribution-lab" / "state"


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration intentionally limited to local state for T003-A."""

    data_dir: Path
    managed_root: Path
    origin: str = "http://127.0.0.1:8000"
    pairing_ttl_seconds: int = 10 * 60
    session_ttl_seconds: int = 8 * 60 * 60
    max_pair_failures: int = 5
    pair_failure_window_seconds: int = 10 * 60
    pair_lockout_seconds: int = 10 * 60
    max_upload_bytes: int = 64 * 1024 * 1024
    max_request_bytes: int = 64 * 1024 * 1024 + 128 * 1024
    max_managed_bytes: int = 1024 * 1024 * 1024
    min_free_bytes: int = 64 * 1024 * 1024
    max_active_uploads: int = 2
    max_active_downloads: int = 8
    transfer_retention_seconds: int = 7 * 24 * 60 * 60

    @classmethod
    def from_environment(cls, *, origin: str = "http://127.0.0.1:8000") -> "Settings":
        data_dir = Path(os.environ.get("LAN_DISTRIBUTION_DATA_DIR", _default_state_dir()))
        managed_root = Path(
            os.environ.get("LAN_DISTRIBUTION_MANAGED_ROOT", data_dir.parent / "managed")
        )
        configured_origin = os.environ.get("LAN_DISTRIBUTION_ORIGIN", origin)
        return cls(data_dir=data_dir, managed_root=managed_root, origin=configured_origin.rstrip("/"))

