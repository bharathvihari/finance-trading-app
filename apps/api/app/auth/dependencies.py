from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import decode_access_token

_bearer = HTTPBearer()


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> CurrentUser:
    """FastAPI dependency — extract and validate the Bearer JWT, return the caller's identity.

    Raises 401 if the token is missing, malformed, or expired.
    Inject into any route that requires authentication:

        @router.get("/protected")
        def protected(current_user: CurrentUser = Depends(get_current_user)):
            ...
    """
    payload = decode_access_token(credentials.credentials)
    return CurrentUser(id=UUID(payload["sub"]), email=payload["email"])
