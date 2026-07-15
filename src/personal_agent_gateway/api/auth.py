import math
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel
import qrcode
import qrcode.image.svg

from personal_agent_gateway.api.dependencies import session_dependency
from personal_agent_gateway.auth_sessions import (
    AuthSessionInfo,
    IssuedAuthSession,
    SessionPrincipal,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    otp: str


class SetupVerifyRequest(BaseModel):
    otp: str


@dataclass
class _LoginFailures:
    count: int = 0
    blocked_until: float = 0.0


class LoginRateLimiter:
    def __init__(self, max_failures: int = 5, block_seconds: int = 60) -> None:
        self._max_failures = max_failures
        self._block_seconds = block_seconds
        self._failures: dict[str, _LoginFailures] = {}

    def retry_after(self, key: str, *, now: float | None = None) -> int | None:
        checked_at = time.monotonic() if now is None else now
        state = self._failures.get(key)
        if state is None or state.blocked_until <= checked_at:
            return None
        return max(1, math.ceil(state.blocked_until - checked_at))

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        checked_at = time.monotonic() if now is None else now
        state = self._failures.setdefault(key, _LoginFailures())
        if state.blocked_until and state.blocked_until <= checked_at:
            state.count = 0
            state.blocked_until = 0.0
        state.count += 1
        if state.count >= self._max_failures:
            state.blocked_until = checked_at + self._block_seconds

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


@router.get("/status")
def status(
    request: Request,
    session: Annotated[str | None, Cookie(alias="agent_session")] = None,
) -> dict[str, object]:
    principal = request.app.state.auth_session_service.validate(session)
    return {
        "authenticated": principal is not None,
        "totp_configured": request.app.state.auth_store.is_totp_enabled(),
        "session": _principal_payload(principal),
    }


@router.post("/login")
def login(request: Request, payload: LoginRequest, response: Response) -> dict[str, bool]:
    key = _client_key(request)
    retry_after = request.app.state.login_rate_limiter.retry_after(key)
    if retry_after is not None:
        _record_auth(request, "auth.login.blocked", "denied", severity="warning")
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    if not _verify_login_secret(request, payload.otp):
        request.app.state.login_rate_limiter.record_failure(key)
        _record_auth(request, "auth.login.failed", "failed", severity="warning")
        raise HTTPException(status_code=401, detail="Unauthorized")
    request.app.state.login_rate_limiter.clear(key)
    issued = _issue_session_cookie(request, response)
    _record_auth(
        request,
        "auth.login.succeeded",
        "succeeded",
        actor_id=issued.principal.id,
    )
    return {"authenticated": True}


@router.post("/setup/start")
def start_setup(
    request: Request,
    token: Annotated[str | None, Query()] = None,
    authorization: Annotated[str | None, Header()] = None,
    web_token: Annotated[str | None, Cookie(alias="agent_web_token")] = None,
) -> dict[str, str]:
    _require_setup_access(request, token, authorization, web_token)
    setup = request.app.state.auth_store.start_totp_setup(account_name="local-owner")
    return {
        "secret": setup.secret,
        "otpauth_uri": setup.otpauth_uri,
        "qr_svg": _qr_svg(setup.otpauth_uri),
    }


@router.post("/setup/verify")
def verify_setup(
    request: Request,
    payload: SetupVerifyRequest,
    response: Response,
    token: Annotated[str | None, Query()] = None,
    authorization: Annotated[str | None, Header()] = None,
    web_token: Annotated[str | None, Cookie(alias="agent_web_token")] = None,
) -> dict[str, object]:
    _require_setup_access(request, token, authorization, web_token)
    result = request.app.state.auth_store.verify_totp_setup(payload.otp)
    if not result.enabled:
        raise HTTPException(status_code=401, detail="Invalid OTP")
    issued = _issue_session_cookie(request, response)
    _record_auth(
        request,
        "auth.setup.completed",
        "succeeded",
        actor_id=issued.principal.id,
    )
    return {"enabled": True, "recovery_codes": result.recovery_codes}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session: Annotated[str | None, Cookie(alias="agent_session")] = None,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, bool]:
    _record_auth(
        request,
        "auth.logout",
        "succeeded",
        actor_id=principal.id,
    )
    request.app.state.auth_session_service.revoke(session)
    response.delete_cookie(key="agent_session")
    return {"authenticated": False}


@router.get("/sessions")
def list_sessions(
    request: Request,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, list[dict[str, object]]]:
    return {
        "sessions": [
            _session_info_payload(item, current=item.id == principal.id)
            for item in request.app.state.auth_session_service.list_sessions()
        ]
    }


@router.delete("/sessions/{session_id}")
def revoke_session(
    request: Request,
    response: Response,
    session_id: str,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, object]:
    _record_auth(
        request,
        "auth.session.revoke_requested",
        "observed",
        actor_id=principal.id,
        resource_id=session_id,
    )
    revoked = request.app.state.auth_session_service.revoke_by_id(session_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Session not found")
    _record_auth(
        request,
        "auth.session.revoked",
        "succeeded",
        actor_id=principal.id,
        resource_id=session_id,
    )
    if session_id == principal.id:
        response.delete_cookie(key="agent_session")
    return {"revoked": True, "session_id": session_id}


@router.post("/sessions/revoke-all")
def revoke_all_sessions(
    request: Request,
    response: Response,
    principal: SessionPrincipal = session_dependency,
) -> dict[str, int]:
    _record_auth(
        request,
        "auth.sessions.revoke_all_requested",
        "observed",
        actor_id=principal.id,
    )
    revoked_count = request.app.state.auth_session_service.revoke_all()
    response.delete_cookie(key="agent_session")
    request.app.state.audit_service.record(
        event_type="auth.sessions.revoked_all",
        action="auth.sessions.revoke_all",
        status="succeeded",
        actor_type="owner",
        actor_id=principal.id,
        session_id=principal.id,
        correlation_id=_correlation_id(request),
        resource_type="auth_session",
        metadata={"revoked_count": revoked_count},
    )
    return {"revoked_count": revoked_count}


def _issue_session_cookie(request: Request, response: Response) -> IssuedAuthSession:
    issued = request.app.state.auth_session_service.issue()
    response.set_cookie(
        key="agent_session",
        value=issued.token,
        httponly=True,
        secure=request.app.state.app_config.cookie_secure,
        samesite="strict",
        max_age=request.app.state.app_config.auth_session_absolute_seconds,
    )
    return issued


def _principal_payload(principal: SessionPrincipal | None) -> dict[str, object] | None:
    if principal is None:
        return None
    return {
        "id": principal.id,
        "created_at": principal.created_at.isoformat(),
        "last_seen_at": principal.last_seen_at.isoformat(),
        "absolute_expires_at": principal.absolute_expires_at.isoformat(),
        "idle_expires_at": principal.idle_expires_at.isoformat(),
    }


def _session_info_payload(session: AuthSessionInfo, *, current: bool) -> dict[str, object]:
    return {
        "id": session.id,
        "created_at": session.created_at.isoformat(),
        "last_seen_at": session.last_seen_at.isoformat(),
        "absolute_expires_at": session.absolute_expires_at.isoformat(),
        "idle_expires_at": session.idle_expires_at.isoformat(),
        "current": current,
    }


def _record_auth(
    request: Request,
    event_type: str,
    status: str,
    *,
    actor_id: str | None = None,
    resource_id: str | None = None,
    severity: str = "info",
) -> None:
    request.app.state.audit_service.record(
        event_type=event_type,
        action=event_type,
        status=status,
        severity=severity,
        actor_type="owner" if actor_id else "anonymous",
        actor_id=actor_id,
        session_id=actor_id,
        correlation_id=_correlation_id(request),
        resource_type="auth_session",
        resource_id=resource_id,
    )


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _verify_login_secret(request: Request, value: str) -> bool:
    if value.isdigit() and len(value) == 6:
        return bool(request.app.state.auth_store.verify_login_code(value))
    return bool(request.app.state.auth_store.use_recovery_code(value))


def _require_setup_access(
    request: Request,
    token: str | None,
    authorization: str | None,
    web_token: str | None,
) -> None:
    expected = request.app.state.app_config.auth_setup_token
    if expected is None:
        expected = request.app.state.app_config.web_token
    provided = token or _bearer_token(authorization) or web_token
    if expected is not None and provided != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value or None


def _qr_svg(value: str) -> str:
    image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")
