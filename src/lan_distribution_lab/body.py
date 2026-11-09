"""Bounded ASGI request-body helpers used before JSON or multipart parsing."""

from __future__ import annotations

from collections.abc import AsyncIterable

from fastapi import Request

from .errors import ApiError


def validate_content_length(value: str | None, maximum: int) -> None:
    """Reject impossible or oversized declared bodies without reading them."""
    if value is None:
        return
    if not value.isascii() or not value.isdecimal():
        raise ApiError(400, "invalid_request", "Content-Length is invalid.")
    if int(value) > maximum:
        raise ApiError(413, "payload_too_large", "Request body is too large.")


async def collect_limited_chunks(chunks: AsyncIterable[bytes], maximum: int) -> bytes:
    """Collect at most ``maximum`` bytes, rejecting before retaining an excess chunk."""
    parts: list[bytes] = []
    total = 0
    async for chunk in chunks:
        if not chunk:
            continue
        if len(chunk) > maximum - total:
            raise ApiError(413, "payload_too_large", "Request body is too large.")
        parts.append(chunk)
        total += len(chunk)
    return b"".join(parts)


async def read_limited_body(request: Request, maximum: int) -> bytes:
    """Read a request via ``Request.stream`` while enforcing a hard cumulative bound."""
    validate_content_length(request.headers.get("content-length"), maximum)
    return await collect_limited_chunks(request.stream(), maximum)
