from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass

import httpx
import jwt

from .errors import TokenExchangeError

log = logging.getLogger(__name__)

GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
TOKEN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:access_token"


@dataclass
class _Cached:
    token: str
    exp: float


class TokenExchanger:
    """RFC 8693 token-exchange client with caching.

    Cache key: (subject_jti, audience). When the upstream user token
    refreshes, its `jti` changes → new cache entry → fresh exchange,
    so downstream tokens never outlive the subject.
    """

    REFRESH_SKEW = 30.0

    def __init__(
        self,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._owns_http = http_client is None
        self._cache: dict[tuple[str, str], _Cached] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def exchange(self, subject_token: str, audience: str) -> str:
        key = (self._subject_jti(subject_token), audience)
        cached = self._cache.get(key)
        now = time.time()
        if cached and cached.exp - self.REFRESH_SKEW > now:
            return cached.token

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached and cached.exp - self.REFRESH_SKEW > now:
                return cached.token
            exchanged = await self._call(subject_token, audience)
            self._cache[key] = exchanged
            return exchanged.token

    async def _call(self, subject_token: str, audience: str) -> _Cached:
        data = {
            "grant_type": GRANT_TOKEN_EXCHANGE,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "subject_token": subject_token,
            "subject_token_type": TOKEN_TYPE_ACCESS,
            "requested_token_type": TOKEN_TYPE_ACCESS,
            "audience": audience,
        }
        try:
            r = await self._http.post(
                self._endpoint,
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise TokenExchangeError(f"Keycloak unreachable: {e}", audience=audience) from e

        if r.status_code >= 400:
            body = _safe_json(r)
            log.info(
                "token-exchange denied by Keycloak aud=%s status=%s body=%s",
                audience, r.status_code, body,
            )
            raise TokenExchangeError(
                body.get("error_description") or body.get("error") or f"HTTP {r.status_code}",
                audience=audience,
                keycloak_error=body,
            )

        payload = r.json()
        new_token = payload["access_token"]
        exp = float(_claim(new_token, "exp", default=time.time() + 60))
        return _Cached(token=new_token, exp=exp)

    @staticmethod
    def _subject_jti(token: str) -> str:
        # Use jti if present, else sub+iat as a fallback — cache key only, not security.
        jti = _claim(token, "jti")
        if jti:
            return str(jti)
        return f"{_claim(token, 'sub', '')}::{_claim(token, 'iat', '')}"


def _claim(token: str, name: str, default=None):
    try:
        return jwt.decode(token, options={"verify_signature": False}).get(name, default)
    except Exception:
        return default


def _safe_json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except Exception:
        return {"error": "non_json_response", "text": r.text[:200]}
