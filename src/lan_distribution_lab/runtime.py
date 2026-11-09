"""Lazy runtime construction so health checks do not create user state."""

from __future__ import annotations

import threading

from .auth import AuthService
from .config import Settings
from .storage import LocalStateStore


class Runtime:
    def __init__(self, settings: Settings, *, store: LocalStateStore | None = None) -> None:
        self.settings = settings
        self.store = store or LocalStateStore(settings)
        self.auth = AuthService(self.store)


class RuntimeProvider:
    """Initializes local state once when an auth/storage endpoint first needs it."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._runtime: Runtime | None = None
        self._lock = threading.Lock()

    def get(self) -> Runtime:
        with self._lock:
            if self._runtime is None:
                self._runtime = Runtime(self.settings)
            return self._runtime
