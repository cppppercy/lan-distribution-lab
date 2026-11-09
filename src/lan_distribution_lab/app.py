"""HTTP application for the implemented T001 and T003-A baselines."""

from __future__ import annotations

import json
import asyncio
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.requests import ClientDisconnect

from .auth import (
    COOKIE_NAME,
    AuthenticatedSession,
    get_current_session,
    get_runtime,
    require_allowed_origin,
    require_read,
    require_read_write,
)
from .body import read_limited_body
from .config import Settings
from .errors import ApiError, error_response
from .files import CHUNK_BYTES, FileRecord, UploadSink
from .multipart import stream_single_file_multipart
from .runtime import Runtime, RuntimeProvider

PAIRING_BODY_MAX_BYTES = 4 * 1024


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_code: str
    client_kind: Literal["browser", "cli"]
    device_label: str | None = None


def create_app(settings: Settings | None = None, *, runtime: Runtime | None = None) -> FastAPI:
    """Create an app without initializing local state until an auth route needs it."""
    selected_settings = settings or Settings.from_environment()
    application = FastAPI(title="LAN Distribution Lab", version="0.2.0")
    provider = RuntimeProvider(selected_settings)
    if runtime is not None:
        provider._runtime = runtime
    application.state.runtime_provider = provider

    @application.exception_handler(ApiError)
    async def handle_api_error(_: Request, error: ApiError) -> Response:
        return error_response(error)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        """Return a public liveness response without operational secrets."""
        return {"status": "ok", "service": "lan-distribution-lab"}

    @application.post("/api/pairings", status_code=201)
    async def create_pairing(request: Request) -> Response:
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise ApiError(415, "unsupported_media_type", "Pairing requests must use application/json.")
        body = await read_limited_body(request, PAIRING_BODY_MAX_BYTES)
        try:
            pairing_request = PairingRequest.model_validate(json.loads(body))
        except (json.JSONDecodeError, ValidationError):
            raise ApiError(422, "invalid_input", "Pairing request is invalid.") from None

        runtime_instance = get_runtime(request)
        if pairing_request.client_kind == "browser":
            require_allowed_origin(request, runtime_instance)
        source = request.client.host if request.client is not None else "unknown"
        created = runtime_instance.auth.exchange_pairing_code(
            pairing_code=pairing_request.pairing_code,
            client_kind=pairing_request.client_kind,
            device_label=pairing_request.device_label,
            source=source,
        )
        payload = {
            "session": {
                "expires_at": created.session.expires_at.isoformat().replace("+00:00", "Z"),
                "scope": created.session.scope,
            }
        }
        if pairing_request.client_kind == "browser":
            response = Response(
                content=json.dumps(payload, separators=(",", ":")),
                status_code=201,
                media_type="application/json",
            )
            response.set_cookie(
                key=COOKIE_NAME,
                value=created.access_token,
                httponly=True,
                samesite="strict",
                secure=False,
                path="/api",
                max_age=runtime_instance.settings.session_ttl_seconds,
            )
        else:
            payload["access_token"] = created.access_token
            response = Response(
                content=json.dumps(payload, separators=(",", ":")),
                status_code=201,
                media_type="application/json",
            )
        return response

    @application.delete("/api/sessions/current", status_code=204)
    def revoke_current_session(
        request: Request,
        authenticated: AuthenticatedSession = Depends(get_current_session),
    ) -> Response:
        runtime_instance = get_runtime(request)
        if authenticated.credential_source == "cookie":
            require_allowed_origin(request, runtime_instance)
        runtime_instance.auth.revoke_session(authenticated.session.session_id)
        response = Response(status_code=204)
        if authenticated.credential_source == "cookie":
            response.delete_cookie(COOKIE_NAME, path="/api")
        return response

    @application.get("/api/files")
    def list_files(
        request: Request,
        authenticated: AuthenticatedSession = Depends(require_read),
    ) -> dict[str, object]:
        limit = _parse_list_limit(request.query_params.get("limit"))
        records = get_runtime(request).files.list_files(limit)
        return {"items": [_file_payload(record) for record in records], "next_cursor": None}

    @application.get("/api/files/{file_id}")
    def get_file_metadata(
        file_id: str,
        request: Request,
        authenticated: AuthenticatedSession = Depends(require_read),
    ) -> dict[str, object]:
        return _file_payload(get_runtime(request).files.get_file(file_id))

    @application.post("/api/uploads", status_code=201)
    async def upload_file(
        request: Request,
        authenticated: AuthenticatedSession = Depends(require_read_write),
    ) -> dict[str, object]:
        runtime_instance = get_runtime(request)
        if authenticated.credential_source == "cookie":
            require_allowed_origin(request, runtime_instance)
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise ApiError(415, "unsupported_media_type", "Uploads must use multipart/form-data.")
        if not runtime_instance.upload_slots.acquire(blocking=False):
            raise ApiError(503, "service_unavailable", "No upload slot is available.")

        sink: UploadSink | None = None
        completed = False
        failure_code = "upload_failed"
        try:
            def on_file_start(_: str) -> None:
                nonlocal sink
                sink = runtime_instance.files.start_upload(authenticated.session.session_id)

            def on_file_data(data: bytes) -> None:
                if sink is None:
                    raise ApiError(422, "invalid_input", "Multipart upload is invalid.")
                runtime_instance.auth.ensure_session_active(authenticated.session.session_id)
                sink.write(data, runtime_instance.settings.max_upload_bytes)

            parsed = await stream_single_file_multipart(
                request,
                maximum_request_bytes=runtime_instance.settings.max_request_bytes,
                on_file_start=on_file_start,
                on_file_data=on_file_data,
            )
            if sink is None:
                raise ApiError(422, "invalid_input", "Multipart upload is invalid.")
            runtime_instance.auth.ensure_session_active(authenticated.session.session_id)
            record, transfer = runtime_instance.files.finish_upload(
                sink, parsed.display_name or parsed.filename
            )
            completed = True
            return {"file": _file_payload(record), "transfer": _transfer_payload(transfer)}
        except (asyncio.CancelledError, ClientDisconnect):
            failure_code = "cancelled"
            raise
        except ApiError as error:
            failure_code = error.code
            raise
        except OSError as error:
            failure_code = "insufficient_storage"
            raise ApiError(507, "insufficient_storage", "Managed storage is unavailable.") from error
        finally:
            try:
                if sink is not None and not completed:
                    runtime_instance.files.fail_upload(sink, failure_code)
            finally:
                runtime_instance.upload_slots.release()

    @application.get("/api/files/{file_id}/content")
    def download_file(
        file_id: str,
        request: Request,
        authenticated: AuthenticatedSession = Depends(require_read),
    ) -> StreamingResponse:
        runtime_instance = get_runtime(request)
        record = runtime_instance.files.get_file(file_id)
        if not runtime_instance.download_slots.acquire(blocking=False):
            raise ApiError(503, "service_unavailable", "No download slot is available.")
        try:
            transfer = runtime_instance.files.start_download(authenticated.session.session_id, record)
        except Exception:
            runtime_instance.download_slots.release()
            raise

        def content() -> object:
            bytes_sent = 0
            completed = False
            try:
                with runtime_instance.files.open_download(record) as source:
                    while True:
                        runtime_instance.auth.ensure_session_active(authenticated.session.session_id)
                        chunk = source.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        bytes_sent += len(chunk)
                        yield chunk
                if bytes_sent != record.size_bytes:
                    raise OSError("downloaded byte count changed")
                runtime_instance.files.complete_download(transfer.transfer_id, bytes_sent)
                completed = True
            except ApiError as error:
                runtime_instance.files.fail_download(transfer.transfer_id, bytes_sent, error.code)
                raise
            except BaseException:
                runtime_instance.files.fail_download(transfer.transfer_id, bytes_sent, "download_failed")
                raise
            finally:
                if not completed:
                    runtime_instance.files.fail_download(transfer.transfer_id, bytes_sent, "download_failed")
                runtime_instance.download_slots.release()

        return StreamingResponse(
            content(),
            media_type="application/octet-stream",
            headers={
                "Content-Length": str(record.size_bytes),
                "ETag": record.etag,
                "Content-Disposition": _content_disposition(record.display_name),
                "X-Transfer-Id": transfer.transfer_id,
            },
        )

    @application.get("/api/transfers/{transfer_id}")
    def get_transfer(
        transfer_id: str,
        request: Request,
        authenticated: AuthenticatedSession = Depends(require_read),
    ) -> dict[str, object]:
        transfer = get_runtime(request).files.get_transfer_for_session(
            authenticated.session.session_id, transfer_id
        )
        return _transfer_payload(transfer)

    return application


def _parse_list_limit(value: str | None) -> int:
    if value is None:
        return 50
    if not value.isdecimal() or not 1 <= int(value) <= 100:
        raise ApiError(422, "invalid_input", "limit must be an integer from 1 to 100.")
    return int(value)


def _file_payload(record: FileRecord) -> dict[str, object]:
    return {
        "file_id": record.file_id,
        "display_name": record.display_name,
        "size_bytes": record.size_bytes,
        "content_sha256": record.content_sha256,
        "etag": record.etag,
        "created_at": record.created_at.isoformat().replace("+00:00", "Z"),
    }


def _transfer_payload(transfer) -> dict[str, object]:
    return {
        "transfer_id": transfer.transfer_id,
        "direction": transfer.direction,
        "state": transfer.state,
        "bytes_transferred": transfer.bytes_transferred,
        "size_bytes": transfer.size_bytes,
        "error_code": transfer.error_code,
        "started_at": transfer.started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": (
            transfer.finished_at.isoformat().replace("+00:00", "Z")
            if transfer.finished_at is not None
            else None
        ),
    }


def _content_disposition(display_name: str) -> str:
    return f"attachment; filename=\"download\"; filename*=UTF-8''{quote(display_name, safe='')}"
app = create_app()
