"""Small bounded multipart reader for the single-file T003 upload contract.

It deliberately consumes ``Request.stream()`` rather than FastAPI's form parser so
the application can reject an oversized body before framework multipart buffering.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from .body import validate_content_length
from .errors import ApiError

MAX_PART_HEADERS = 16 * 1024
MAX_FIELD_BYTES = 1024


@dataclass(frozen=True, slots=True)
class MultipartUpload:
    filename: str
    display_name: str | None


async def stream_single_file_multipart(
    request: Request,
    *,
    maximum_request_bytes: int,
    on_file_start: Callable[[str], None],
    on_file_data: Callable[[bytes], None],
) -> MultipartUpload:
    """Parse exactly one ``file`` part and an optional small ``display_name`` field."""
    boundary = _boundary_from_content_type(request.headers.get("content-type", ""))
    validate_content_length(request.headers.get("content-length"), maximum_request_bytes)
    opening = b"--" + boundary + b"\r\n"
    delimiter = b"\r\n--" + boundary
    buffer = bytearray()
    received = 0
    state = "opening"
    current_name: str | None = None
    current_filename: str | None = None
    field_data = bytearray()
    file_seen = False
    display_name: str | None = None
    filename: str | None = None

    async for chunk in request.stream():
        if not chunk:
            continue
        if len(chunk) > maximum_request_bytes - received:
            raise ApiError(413, "payload_too_large", "Request body is too large.")
        received += len(chunk)
        buffer.extend(chunk)

        while True:
            if state == "boundary_end":
                if len(buffer) < 2:
                    break
                state = _consume_boundary_end(buffer)
                if state == "complete":
                    if buffer and buffer != b"\r\n":
                        raise _invalid_multipart()
                    if not file_seen or filename is None:
                        raise _invalid_multipart()
                    return MultipartUpload(filename=filename, display_name=display_name)

            if state == "opening":
                if len(buffer) < len(opening):
                    break
                if not buffer.startswith(opening):
                    raise _invalid_multipart()
                del buffer[: len(opening)]
                state = "headers"

            if state == "headers":
                separator = buffer.find(b"\r\n\r\n")
                if separator < 0:
                    if len(buffer) > MAX_PART_HEADERS:
                        raise _invalid_multipart()
                    break
                raw_headers = bytes(buffer[:separator])
                del buffer[: separator + 4]
                current_name, current_filename = _parse_part_headers(raw_headers)
                field_data.clear()
                if current_name == "file":
                    if file_seen or not current_filename:
                        raise _invalid_multipart()
                    file_seen = True
                    filename = current_filename
                    on_file_start(filename)
                    state = "file"
                elif current_name == "display_name":
                    state = "field"
                else:
                    raise _invalid_multipart()

            position = buffer.find(delimiter)
            if position < 0:
                keep = len(delimiter) + 4
                if len(buffer) <= keep:
                    if state == "field" and len(field_data) + len(buffer) > MAX_FIELD_BYTES:
                        raise _invalid_multipart()
                    break
                data = bytes(buffer[:-keep])
                del buffer[:-keep]
                if state == "file":
                    on_file_data(data)
                else:
                    if len(field_data) + len(data) > MAX_FIELD_BYTES:
                        raise _invalid_multipart()
                    field_data.extend(data)
                continue

            data = bytes(buffer[:position])
            del buffer[: position + len(delimiter)]
            if state == "file":
                on_file_data(data)
            else:
                if len(field_data) + len(data) > MAX_FIELD_BYTES:
                    raise _invalid_multipart()
                field_data.extend(data)
                try:
                    display_name = field_data.decode("utf-8")
                except UnicodeDecodeError:
                    raise ApiError(422, "invalid_input", "display_name is invalid.") from None

            if len(buffer) < 2:
                state = "boundary_end"
                break
            state = _consume_boundary_end(buffer)
            if state == "complete":
                if buffer and buffer != b"\r\n":
                    raise _invalid_multipart()
                if not file_seen or filename is None:
                    raise _invalid_multipart()
                return MultipartUpload(filename=filename, display_name=display_name)

    raise _invalid_multipart()


def _boundary_from_content_type(value: str) -> bytes:
    if not value.lower().startswith("multipart/form-data"):
        raise ApiError(415, "unsupported_media_type", "Uploads must use multipart/form-data.")
    match = re.search(r"(?:^|;)\s*boundary=(?:\"([^\"]+)\"|([^;\s]+))", value, re.IGNORECASE)
    if match is None:
        raise ApiError(422, "invalid_input", "Multipart boundary is invalid.")
    try:
        boundary = (match.group(1) or match.group(2)).encode("ascii", "strict")
    except UnicodeEncodeError:
        raise ApiError(422, "invalid_input", "Multipart boundary is invalid.") from None
    if not 1 <= len(boundary) <= 70:
        raise ApiError(422, "invalid_input", "Multipart boundary is invalid.")
    return boundary


def _parse_part_headers(raw_headers: bytes) -> tuple[str, str | None]:
    try:
        lines = raw_headers.decode("latin-1").split("\r\n")
    except UnicodeDecodeError:
        raise _invalid_multipart() from None
    disposition = next((line for line in lines if line.lower().startswith("content-disposition:")), None)
    if disposition is None or "form-data" not in disposition.lower():
        raise _invalid_multipart()
    attributes: dict[str, str] = {}
    for piece in disposition.split(";")[1:]:
        key, separator, value = piece.strip().partition("=")
        if not separator:
            continue
        attributes[key.lower()] = value.strip().strip('"')
    name = attributes.get("name")
    if name not in {"file", "display_name"}:
        raise _invalid_multipart()
    return name, attributes.get("filename")


def _consume_boundary_end(buffer: bytearray) -> str:
    if buffer.startswith(b"--"):
        del buffer[:2]
        if buffer.startswith(b"\r\n"):
            del buffer[:2]
        return "complete"
    if buffer.startswith(b"\r\n"):
        del buffer[:2]
        return "headers"
    raise _invalid_multipart()


def _invalid_multipart() -> ApiError:
    return ApiError(422, "invalid_input", "Multipart upload is invalid.")
