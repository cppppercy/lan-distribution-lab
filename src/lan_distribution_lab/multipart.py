"""Small bounded multipart reader for the single-file T003 upload contract.

It deliberately consumes ``Request.stream()`` rather than FastAPI's form parser so
the application can reject an oversized body before framework multipart buffering.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes

from fastapi import Request

from .body import validate_content_length
from .errors import ApiError

MAX_PART_HEADERS = 16 * 1024
MAX_FIELD_BYTES = 1024
MAX_STREAM_CHUNK_BYTES = 64 * 1024


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
    """Parse one file and an optional display name without retaining file content.

    A delimiter-looking byte sequence is only consumed when its following two bytes
    are the multipart continuation (``CRLF``) or closing (``--``) marker.  This is
    important for binary file content such as ``\\r\\n--boundaryXXX``.
    """
    boundary = _boundary_from_content_type(request.headers.get("content-type", ""))
    validate_content_length(request.headers.get("content-length"), maximum_request_bytes)
    opening = b"--" + boundary + b"\r\n"
    delimiter = b"\r\n--" + boundary
    buffer = bytearray()
    state = "opening"
    file_seen = False
    display_seen = False
    display_name: str | None = None
    filename: str | None = None
    field_data = bytearray()

    def consume_part_data(data: bytes) -> None:
        if state == "file":
            on_file_data(data)
            return
        if len(field_data) + len(data) > MAX_FIELD_BYTES:
            raise _invalid_multipart()
        field_data.extend(data)

    def finish_part() -> None:
        nonlocal display_name
        if state == "field":
            try:
                display_name = field_data.decode("utf-8")
            except UnicodeDecodeError:
                raise ApiError(422, "invalid_input", "display_name is invalid.") from None

    async for chunk in _bounded_chunks(request, maximum_request_bytes):
        buffer.extend(chunk)

        while True:
            if state == "complete":
                # RFC 7578 permits only an optional final CRLF after ``--boundary--``.
                if len(buffer) > 2 or not b"\r\n".startswith(buffer):
                    raise _invalid_multipart()
                break

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
                    if display_seen:
                        raise _invalid_multipart()
                    display_seen = True
                    state = "field"
                else:  # Defensive guard for future extensions to _parse_part_headers.
                    raise _invalid_multipart()

            if state == "boundary_end":
                if len(buffer) < 2:
                    break
                suffix = bytes(buffer[:2])
                del buffer[:2]
                if suffix == b"--":
                    state = "complete"
                elif suffix == b"\r\n":
                    state = "headers"
                else:
                    raise _invalid_multipart()
                continue

            # Keep only a small possible delimiter suffix.  File bytes are sent to
            # the callback as soon as they are known not to be a real boundary.
            position = buffer.find(delimiter)
            if position < 0:
                keep = len(delimiter) + 1
                if len(buffer) <= keep:
                    break
                data = bytes(buffer[:-keep])
                del buffer[:-keep]
                consume_part_data(data)
                continue

            boundary_end = position + len(delimiter)
            if len(buffer) < boundary_end + 2:
                if position:
                    data = bytes(buffer[:position])
                    del buffer[:position]
                    consume_part_data(data)
                break

            suffix = bytes(buffer[boundary_end : boundary_end + 2])
            if suffix not in {b"--", b"\r\n"}:
                # This is binary content that merely starts like a delimiter.  The
                # first byte cannot begin another delimiter, so it is safe to flush.
                data = bytes(buffer[: position + 1])
                del buffer[: position + 1]
                consume_part_data(data)
                continue

            consume_part_data(bytes(buffer[:position]))
            del buffer[:boundary_end]
            finish_part()
            state = "boundary_end"

    if state != "complete" or buffer not in (bytearray(), bytearray(b"\r\n")):
        raise _invalid_multipart()
    if not file_seen or filename is None:
        raise _invalid_multipart()
    return MultipartUpload(filename=filename, display_name=display_name)


async def _bounded_chunks(request: Request, maximum_request_bytes: int) -> AsyncIterator[bytes]:
    """Enforce the total body cap and avoid a parser buffer sized by an ASGI chunk."""
    received = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        if len(chunk) > maximum_request_bytes - received:
            raise ApiError(413, "payload_too_large", "Request body is too large.")
        received += len(chunk)
        for start in range(0, len(chunk), MAX_STREAM_CHUNK_BYTES):
            yield chunk[start : start + MAX_STREAM_CHUNK_BYTES]


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
    disposition = next(
        (line for line in raw_headers.split(b"\r\n") if line.lower().startswith(b"content-disposition:")),
        None,
    )
    if disposition is None:
        raise _invalid_multipart()
    _, _, raw_value = disposition.partition(b":")
    pieces = _split_disposition(raw_value.strip())
    if not pieces or pieces[0].lower() != b"form-data":
        raise _invalid_multipart()

    attributes: dict[str, bytes] = {}
    for piece in pieces[1:]:
        key, separator, value = piece.strip().partition(b"=")
        if not separator:
            raise _invalid_multipart()
        try:
            name = key.decode("ascii", "strict").lower()
        except UnicodeDecodeError:
            raise _invalid_multipart() from None
        if name in attributes:
            raise _invalid_multipart()
        attributes[name] = _unquote_parameter(value.strip())

    try:
        name = attributes["name"].decode("ascii", "strict")
    except (KeyError, UnicodeDecodeError):
        raise _invalid_multipart() from None
    if name not in {"file", "display_name"}:
        raise _invalid_multipart()
    if name == "display_name" and ("filename" in attributes or "filename*" in attributes):
        raise _invalid_multipart()

    if "filename*" in attributes:
        filename = _decode_extended_filename(attributes["filename*"])
    elif "filename" in attributes:
        try:
            filename = attributes["filename"].decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise ApiError(422, "invalid_input", "filename is invalid.") from None
    else:
        filename = None
    return name, filename


def _split_disposition(value: bytes) -> list[bytes]:
    pieces: list[bytes] = []
    current = bytearray()
    quoted = False
    escaped = False
    for byte in value:
        if escaped:
            current.append(byte)
            escaped = False
        elif byte == 0x5C and quoted:
            current.append(byte)
            escaped = True
        elif byte == 0x22:
            current.append(byte)
            quoted = not quoted
        elif byte == 0x3B and not quoted:
            pieces.append(bytes(current).strip())
            current.clear()
        else:
            current.append(byte)
    if quoted or escaped:
        raise _invalid_multipart()
    pieces.append(bytes(current).strip())
    return pieces


def _unquote_parameter(value: bytes) -> bytes:
    if not value.startswith(b'"'):
        return value
    if len(value) < 2 or not value.endswith(b'"'):
        raise _invalid_multipart()
    result = bytearray()
    escaped = False
    for byte in value[1:-1]:
        if escaped:
            result.append(byte)
            escaped = False
        elif byte == 0x5C:
            escaped = True
        else:
            result.append(byte)
    if escaped:
        raise _invalid_multipart()
    return bytes(result)


def _decode_extended_filename(value: bytes) -> str:
    charset, separator, encoded = value.partition(b"''")
    if not separator or charset.lower() != b"utf-8":
        raise ApiError(422, "invalid_input", "filename is invalid.")
    if re.search(br"%(?![0-9A-Fa-f]{2})", encoded):
        raise ApiError(422, "invalid_input", "filename is invalid.")
    try:
        return unquote_to_bytes(encoded).decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise ApiError(422, "invalid_input", "filename is invalid.") from None


def _invalid_multipart() -> ApiError:
    return ApiError(422, "invalid_input", "Multipart upload is invalid.")
