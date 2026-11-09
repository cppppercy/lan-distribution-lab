"""HTTP application for the implemented T001 and T003-A baselines."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from .auth import (
    COOKIE_NAME,
    AuthenticatedSession,
    get_current_session,
    get_runtime,
    require_allowed_origin,
)
from .config import Settings
from .errors import ApiError, error_response
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
        content_length = request.headers.get("content-length")
        if content_length is not None and _content_length_exceeds_limit(content_length):
            raise ApiError(413, "payload_too_large", "Pairing request is too large.")

        body = await request.body()
        if len(body) > PAIRING_BODY_MAX_BYTES:
            raise ApiError(413, "payload_too_large", "Pairing request is too large.")
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

    return application


def _content_length_exceeds_limit(value: str) -> bool:
    try:
        return int(value) > PAIRING_BODY_MAX_BYTES
    except ValueError:
        return True


app = create_app()
