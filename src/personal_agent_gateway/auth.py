from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Query, Response
from fastapi.params import Depends as DependsParam


def require_token(expected_token: str) -> DependsParam:
    def dependency(
        response: Response,
        token: Annotated[str | None, Query()] = None,
        authorization: Annotated[str | None, Header()] = None,
        cookie_token: Annotated[str | None, Cookie(alias="agent_web_token")] = None,
    ) -> None:
        bearer_token = _bearer_token(authorization)
        provided_token = token or bearer_token or cookie_token

        if provided_token != expected_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

        if token == expected_token:
            response.set_cookie(
                key="agent_web_token",
                value=expected_token,
                httponly=True,
                samesite="lax",
            )

    return Depends(dependency)


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    if not value:
        return None
    return value
