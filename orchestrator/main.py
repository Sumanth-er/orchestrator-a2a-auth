"""Orchestrator — the only server the browser talks to.

Responsibilities:
  - Serve the static chatbot UI.
  - Authenticate user token at /chat (Keycloak JWKS).
  - Ask LLM to pick an agent.
  - Token-exchange to the agent's audience, call via a2a-sdk, never raise.
  - Stream per-agent chips + final reply back as SSE.

NOTE: This does NOT enforce per-agent authorization. Agents do that.
The only check here is "is the token a valid Keycloak JWT from our realm".
"""
from __future__ import annotations

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

from orchestrator import router_llm
from orchestrator.a2a_dispatcher import Dispatcher

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

    async def stream():
        yield _sse("user_authenticated", {"username": username})

        selection = await router_llm.pick_agent(user_message)
        if selection.agent is None:
            yield _sse("no_agent", {"rationale": selection.rationale})
            reply = await router_llm.compose_reply(
                user_message, None, "ok", None, None
            )
            yield _sse("reply", {"text": reply})
            return

        entry = selection.agent
        yield _sse("agent_selected", {
            "agent": entry.name,
            "rationale": selection.rationale,
        })

        result = await dispatcher.call(entry, user_token=token, user_text=user_message)
        yield _sse("agent_result", result.to_event())

        reply = await router_llm.compose_reply(
            user_message=user_message,
            agent_name=entry.name,
            status=result.status,
            agent_output=result.output,
            reason=result.reason,
        )
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
