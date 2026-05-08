"""Calls A2A agents using the SDK's own client stack.

Key choices:
  - `ClientFactory` + `ClientConfig(httpx_client=...)` from `a2a.client`
    — we do NOT subclass or reimplement the client.
  - `bearer_httpx_client(token)` is the httpx hook that injects
    `Authorization: Bearer <exchanged-token>` on every request.
  - Auth denials never raise to the caller — they become `AgentCallResult`
    so the UI can render chips and the LLM can respond gracefully.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from typing import Literal
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory

from shared.a2a_auth import TokenExchanger, bearer_httpx_client
from shared.a2a_auth.errors import TokenExchangeError

from orchestrator.registry import AgentEntry

log = logging.getLogger(__name__)

Status = Literal["ok", "denied", "error"]


@dataclass
class AgentCallResult:
    agent: str
    status: Status
    output: str | None = None
    reason: str | None = None
    error_code: str | None = None
    elapsed_ms: int = 0

    def to_event(self) -> dict:
        return asdict(self)


class Dispatcher:
    def __init__(self, exchanger: TokenExchanger):
        self._exchanger = exchanger

    async def call(
        self,
        entry: AgentEntry,
        *,
        user_token: str,
        user_text: str,
    ) -> AgentCallResult:
        t0 = time.perf_counter()
        try:
            exchanged = await self._exchanger.exchange(user_token, entry.audience)
        except TokenExchangeError as e:
            return self._done(entry, "denied",
                              reason=f"You don't have access to the {entry.name} agent.",
                              error_code=e.code, t0=t0)

        try:
            output = await self._invoke(entry, exchanged, user_text)
            return self._done(entry, "ok", output=output, t0=t0)
        except Exception as e:
            # The a2a-sdk wraps httpx errors in its own exception classes, so we
            # can't rely on `except httpx.HTTPStatusError` alone. Walk the cause
            # chain looking for an httpx.Response we can inspect.
            http_err = _find_http_status_error(e)
            if http_err is not None:
                return self._map_http_error(entry, http_err, t0)
            log.exception("dispatcher: unexpected error calling %s", entry.name)
            return self._done(entry, "error",
                              reason=f"Agent {entry.name} unreachable: {e}",
                              error_code="AGENT_UNREACHABLE", t0=t0)

    def _map_http_error(self, entry, http_err, t0) -> "AgentCallResult":
        status = http_err.status
        body = http_err.body
        if status == 401:
            # 401 = bad token (signature/aud/exp). Surfacing as "denied" because
            # from the user's POV, they can't reach this agent — the cause is
            # almost always missing aud-<agent> scope on the orchestrator client.
            return self._done(entry, "denied",
                              reason=body.get("message") or "Token rejected by agent (audience or signature).",
                              error_code=body.get("code") or "UNAUTHORIZED", t0=t0)
        if status == 403:
            return self._done(entry, "denied",
                              reason=body.get("message") or f"No access to {entry.name}.",
                              error_code=body.get("code") or "ACCESS_DENIED", t0=t0)
        return self._done(entry, "error",
                          reason=f"Agent {entry.name} returned HTTP {status}.",
                          error_code="AGENT_ERROR", t0=t0)

    async def _invoke(self, entry: AgentEntry, token: str, text: str) -> str:
        from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest

        async with bearer_httpx_client(token) as http:
            resolver = A2ACardResolver(httpx_client=http, base_url=entry.url)
            card = await resolver.get_agent_card()

            factory = ClientFactory(ClientConfig(httpx_client=http, streaming=False))
            client = factory.create(card)
            try:
                request = SendMessageRequest(
                    message=Message(
                        role=Role.ROLE_USER,
                        parts=[Part(text=text)],
                        message_id=uuid4().hex,
                    )
                )
                collected: list[str] = []
                async for chunk in client.send_message(request):
                    task, _ = chunk
                    collected.extend(_harvest_text(task))
                return "\n".join(t for t in collected if t) or "(no content)"
            finally:
                await client.close()

    @staticmethod
    def _done(entry, status, *, output=None, reason=None, error_code=None, t0):
        return AgentCallResult(
            agent=entry.name,
            status=status,
            output=output,
            reason=reason,
            error_code=error_code,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )


class _A2AHttpError(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body

    @classmethod
    def from_response(cls, r: httpx.Response) -> "_A2AHttpError":
        try:
            body = r.json()
        except Exception:
            body = {"message": r.text[:200]}
        return cls(r.status_code, body)


def _find_http_status_error(exc: BaseException) -> _A2AHttpError | None:
    """Walk the __cause__ / __context__ chain looking for HTTP status info.

    a2a-sdk 1.0.0 wraps transport errors in its own exception classes, so a
    real httpx.HTTPStatusError ends up several layers deep. We also look for
    exceptions that carry a `.response` attribute (httpx-style) or
    `.status_code` directly (some SDK errors).
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))

        # Direct httpx error: has a .response with status_code
        resp = getattr(cur, "response", None)
        if resp is not None and hasattr(resp, "status_code"):
            return _A2AHttpError.from_response(resp)

        # SDK error that exposes status_code directly
        status = getattr(cur, "status_code", None)
        if isinstance(status, int) and status >= 400:
            body = {"message": str(cur)[:200]}
            return _A2AHttpError(status, body)

        # Last resort: parse out a "403"/"401" from the message string. This
        # catches the case where a2a-sdk re-raises with a stringified httpx
        # error but loses the .response reference.
        msg = str(cur)
        for code in (401, 403):
            if f"{code}" in msg and ("Forbidden" in msg or "Unauthorized" in msg or "Client error" in msg):
                return _A2AHttpError(code, {"message": msg[:200]})

        cur = cur.__cause__ or cur.__context__
    return None


def _harvest_text(task) -> list[str]:
    """Pull text out of a Task / artifact tree regardless of exact proto shape."""
    out: list[str] = []
    artifacts = getattr(task, "artifacts", None) or []
    for art in artifacts:
        parts = getattr(art, "parts", None) or []
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                out.append(t)
    status = getattr(task, "status", None)
    msg = getattr(status, "message", None) if status else None
    if msg:
        for p in getattr(msg, "parts", []) or []:
            t = getattr(p, "text", None)
            if t:
                out.append(t)
    return out
