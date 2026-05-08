"""Orchestrator — the only server the browser talks to.

Responsibilities:
  - Serve the static chatbot UI.
  - Authenticate user token at /chat (Keycloak JWKS).
  - Run a create_agent runtime that picks 1..N specialist agents per turn
    via the call_agent tool (see orchestrator/agent_runtime.py).
  - Token-exchange to each agent's audience, call via a2a-sdk, never raise.
  - Stream per-agent chips + final reply back as SSE.

NOTE: This does NOT enforce per-agent authorization. Agents do that.
The only check here is "is the token a valid Keycloak JWT from our realm".
"""
from __future__ import annotations

import shared.proto_compat  # noqa: F401  MUST be first — see module docstring

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # must run before any os.environ reads below

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from shared.a2a_auth import (
    JwtValidator,
    KeycloakSettings,
    TokenExchanger,
)
from shared.a2a_auth.errors import AuthError, UnauthorizedError

from orchestrator.a2a_dispatcher import Dispatcher
from orchestrator.agent_runtime import build_runtime, run as run_runtime

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("orchestrator")


class ChatRequest(BaseModel):
    message: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = KeycloakSettings()
    app.state.settings = settings
    app.state.validator = JwtValidator(issuer=settings.issuer, jwks_uri=settings.jwks_uri)
    app.state.exchanger = TokenExchanger(
        token_endpoint=settings.token_endpoint,
        client_id=os.environ["KC_ORCHESTRATOR_CLIENT_ID"],
        client_secret=os.environ["KC_ORCHESTRATOR_CLIENT_SECRET"],
    )
    app.state.dispatcher = Dispatcher(exchanger=app.state.exchanger)
    try:
        yield
    finally:
        await app.state.exchanger.aclose()


app = FastAPI(lifespan=lifespan)


def _authenticate(request: Request) -> tuple[str, dict]:
    """Front-door authentication only — does NOT check agent access."""
    try:
        token = JwtValidator.extract_bearer(request.headers.get("authorization"))
        claims = request.app.state.validator.validate(token)  # no audience check here
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return token, claims


@app.get("/api/config")
async def config():
    """Public config for the UI — Keycloak URL + realm + UI client id."""
    s: KeycloakSettings = app.state.settings
    return {
        "kc_url": s.kc_url,
        "realm": s.kc_realm,
        "client_id": os.environ.get("KC_UI_CLIENT_ID", "a2a-ui"),
    }


@app.get("/api/me")
async def me(request: Request):
    _token, claims = _authenticate(request)
    return {
        "username": claims.get("preferred_username"),
        "roles": claims.get("realm_access", {}).get("roles", []),
        "sub": claims.get("sub"),
    }


@app.exception_handler(HTTPException)
async def on_http_exception(_req, exc: HTTPException):
    # Return JSON detail rather than Starlette's default string body.
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.post("/chat")
async def chat(request: Request):
    payload = await request.json()
    user_message = (payload or {}).get("message", "").strip()
    if not user_message:
        raise HTTPException(400, "message is required")

    token, claims = _authenticate(request)
    # preferred_username comes from the `profile` scope; fall back to sub if missing
    # so the UI never displays a fake-looking literal "user".
    username = claims.get("preferred_username") or claims.get("sub", "")[:8] or "anonymous"

    dispatcher: Dispatcher = request.app.state.dispatcher

    # Bus that the call_agent tool pushes SSE events into as it runs.
    # Decouples tool execution (LangGraph node) from the SSE generator.
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
    chip_count = 0

    async def event_sink(name: str, data: dict) -> None:
        nonlocal chip_count
        if name == "agent_selected":
            chip_count += 1
        await queue.put((name, data))

    runtime = build_runtime(
        dispatcher=dispatcher,
        user_token=token,
        event_sink=event_sink,
    )

    async def stream():
        yield _sse("user_authenticated", {"username": username})

        agent_task = asyncio.create_task(run_runtime(runtime, user_message))

        # Fan tool-emitted events while the runtime executes; finish when
        # the runtime is done AND the queue is drained.
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.1)
                if item is None:
                    break
                name, data = item
                yield _sse(name, data)
            except asyncio.TimeoutError:
                if agent_task.done() and queue.empty():
                    break

        try:
            reply = agent_task.result()
        except Exception as e:                              # noqa: BLE001
            log.exception("orchestrator: runtime failed")
            reply = f"Sorry, something went wrong: {e}"

        # Pure small-talk turns produce no chip; emit no_agent so the UI's
        # chip row still renders something (matches legacy behaviour).
        if chip_count == 0:
            yield _sse("no_agent", {"rationale": "answered directly"})

        yield _sse("reply", {"text": reply})

    return EventSourceResponse(stream())


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data)}


# Static files last — so API routes win.
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "orchestrator.main:app",
        host="127.0.0.1",
        port=int(os.environ.get("ORCHESTRATOR_PORT", 3000)),
        reload=False,
    )
