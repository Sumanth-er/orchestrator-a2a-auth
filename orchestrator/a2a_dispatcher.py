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
        except _A2AHttpError as e:
            if e.status == 401:
                return self._done(entry, "error",
                                  reason="Token rejected by agent.",
                                  error_code="UNAUTHORIZED", t0=t0)
            if e.status == 403:
                return self._done(entry, "denied",
                                  reason=e.body.get("message") or f"No access to {entry.name}.",
                                  error_code=e.body.get("code") or "ACCESS_DENIED", t0=t0)
            return self._done(entry, "error",
                              reason=f"Agent {entry.name} returned HTTP {e.status}.",
                              error_code="AGENT_ERROR", t0=t0)
        except Exception as e:
            log.exception("dispatcher: unexpected error calling %s", entry.name)
            return self._done(entry, "error",
                              reason=f"Agent {entry.name} unreachable: {e}",
                              error_code="AGENT_UNREACHABLE", t0=t0)

    async def _invoke(self, entry: AgentEntry, token: str, text: str) -> str:
        from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest

        async with bearer_httpx_client(token) as http:
            try:
                resolver = A2ACardResolver(httpx_client=http, base_url=entry.url)
                card = await resolver.get_agent_card()
            except httpx.HTTPStatusError as e:
                raise _A2AHttpError.from_response(e.response) from e

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
                try:
                    async for chunk in client.send_message(request):
                        task, _ = chunk
                        collected.extend(_harvest_text(task))
                except httpx.HTTPStatusError as e:
                    raise _A2AHttpError.from_response(e.response) from e
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
