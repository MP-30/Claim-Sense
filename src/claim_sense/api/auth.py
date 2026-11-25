"""Cognito access token verification for claim routes."""

from functools import lru_cache
import os
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt.exceptions import PyJWKClientConnectionError


bearer = HTTPBearer(auto_error=False)


def auth_required() -> bool:
    return os.getenv("CLAIMS_AUTH_REQUIRED", "").lower() == "true" or bool(os.getenv("COGNITO_CLIENT_ID"))


@lru_cache(maxsize=4)
def jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, timeout=5)


def require_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str | None:
    if not auth_required():
        return None
    issuer = os.getenv("COGNITO_ISSUER")
    client_id = os.getenv("COGNITO_CLIENT_ID")
    if not issuer or not client_id:
        raise HTTPException(503, "Cognito sign-in is not configured.")
    unauthorized = HTTPException(401, "Sign in with a valid access token.", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise unauthorized

    token = credentials.credentials
    key_url = os.getenv("COGNITO_JWKS_URL") or f"{issuer}/.well-known/jwks.json"
    try:
        signing_key = jwks(key_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["iss", "exp", "sub"], "verify_aud": False},
        )
    except PyJWKClientConnectionError as error:
        raise HTTPException(503, "Cognito signing keys are temporarily unavailable.") from error
    except jwt.PyJWTError as error:
        raise unauthorized from error

    username = claims.get("username")
    if claims.get("token_use") != "access" or claims.get("client_id") != client_id:
        raise unauthorized
    if not isinstance(username, str) or not username:
        raise unauthorized
    return username
