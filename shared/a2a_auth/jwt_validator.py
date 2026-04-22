from __future__ import annotations

import jwt
from jwt import PyJWKClient

from .errors import UnauthorizedError


class JwtValidator:
    """Validate Keycloak JWTs using cached JWKS.

    Signature + issuer + exp are always checked. Audience is checked only
    when `expected_audience` is provided (orchestrator at the front door
    doesn't check aud; agents do).
    """

    def __init__(self, issuer: str, jwks_uri: str):
        self._issuer = issuer
        self._jwks = PyJWKClient(jwks_uri, cache_keys=True, lifespan=600)

    def validate(self, token: str, expected_audience: str | None = None) -> dict:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token).key
        except Exception as e:
            raise UnauthorizedError(f"JWKS lookup failed: {e}") from e

        options = {"verify_aud": expected_audience is not None}
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=expected_audience,
                options=options,
            )
        except jwt.ExpiredSignatureError:
            raise UnauthorizedError("Token expired")
        except jwt.InvalidAudienceError:
            raise UnauthorizedError(
                f"Token audience mismatch (expected {expected_audience!r})"
            )
        except jwt.InvalidTokenError as e:
            raise UnauthorizedError(f"Invalid token: {e}") from e
        return claims

    @staticmethod
    def extract_realm_roles(claims: dict) -> list[str]:
        return list(claims.get("realm_access", {}).get("roles", []))

    @staticmethod
    def extract_bearer(authorization_header: str | None) -> str:
        if not authorization_header:
            raise UnauthorizedError("Missing Authorization header")
        parts = authorization_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise UnauthorizedError("Authorization header must be 'Bearer <token>'")
        return parts[1].strip()
