import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
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
    if not request.app.state.auth_store.verify_login_code(payload.otp):
        raise HTTPException(status_code=401, detail="Unauthorized")
    response.set_cookie(
        key="agent_session",
        value=secrets.token_urlsafe(32),
        httponly=True,
        secure=request.app.state.app_config.cookie_secure,
        samesite="strict",
    )
    return {"authenticated": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(key="agent_session")
    return {"authenticated": False}
