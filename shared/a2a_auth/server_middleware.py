from __future__ import annotations

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from .errors import AccessDeniedError, AuthError, UnauthorizedError
from .jwt_validator import JwtValidator

log = logging.getLogger(__name__)


class KeycloakAuthMiddleware(BaseHTTPMiddleware):
    """Drop-in auth for any A2A agent server.

    Validates:
      - signature + issuer + exp (via JWKS)
      - `aud` claim matches this agent's audience
      - user holds at least one of `required_roles` (realm roles)

    Leaves the decoded claims on `request.state.user` for the executor.
    Public paths (agent card discovery) are whitelisted.
    """

    PUBLIC_PATHS = (
        "/.well-known/agent-card.json",
        "/.well-known/agent.json",
        "/agent-card",
    )

    def __init__(
        self,
        app: ASGIApp,
        *,
        validator: JwtValidator,
        expected_audience: str,
        required_roles: list[str],
        public_paths: tuple[str, ...] = PUBLIC_PATHS,
    ):
        super().__init__(app)
        self._validator = validator
        self._audience = expected_audience
        self._required_roles = list(required_roles)
        self._public = public_paths

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._public:
            return await call_next(request)

        try:
            token = self._validator.extract_bearer(request.headers.get("authorization"))
            claims = self._validator.validate(token, expected_audience=self._audience)
            user_roles = self._validator.extract_realm_roles(claims)
            if self._required_roles and not any(r in user_roles for r in self._required_roles):
                raise AccessDeniedError(
                    f"User '{claims.get('preferred_username')}' lacks required role(s) "
                    f"{self._required_roles}",
                    required_roles=self._required_roles,
                    user_roles=user_roles,
                    audience=self._audience,
                )
            request.state.user = claims
            request.state.user_token = token
        except AuthError as e:
            log.info("auth denied: %s (%s)", e.code, e.message)
            return JSONResponse(status_code=e.status_code, content=e.to_dict())

        return await call_next(request)
