import secrets
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel
import qrcode
import qrcode.image.svg


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    otp: str


class SetupVerifyRequest(BaseModel):
    otp: str


@router.get("/status")
def status(
    request: Request,
    session: Annotated[str | None, Cookie(alias="agent_session")] = None,
) -> dict[str, object]:
    return {
        "authenticated": bool(session),
        "totp_configured": request.app.state.auth_store.is_totp_enabled(),
    }


@router.post("/login")
def login(request: Request, payload: LoginRequest, response: Response) -> dict[str, bool]:
    if not _verify_login_secret(request, payload.otp):
        raise HTTPException(status_code=401, detail="Unauthorized")
    _set_session_cookie(request, response)
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
    _set_session_cookie(request, response)
    return {"enabled": True, "recovery_codes": result.recovery_codes}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(key="agent_session")
    return {"authenticated": False}


def _set_session_cookie(request: Request, response: Response) -> None:
    response.set_cookie(
        key="agent_session",
        value=secrets.token_urlsafe(32),
        httponly=True,
        secure=request.app.state.app_config.cookie_secure,
        samesite="strict",
    )


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
