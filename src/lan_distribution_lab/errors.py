"""API-safe error types and response helpers."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi.responses import JSONResponse


class ApiError(Exception):
    """An expected failure whose public text is safe to return to a client."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


def error_response(error: ApiError) -> JSONResponse:
    """Create the contract's uniform, non-sensitive error response."""
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
        headers=error.headers,
    )

