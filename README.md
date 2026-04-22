# A2A + Keycloak Demo

End-to-end demo of the **Agent2Agent (A2A) protocol** with proper Keycloak auth:

- Browser → Keycloak (OIDC + PKCE) → browser gets access_token.
- Orchestrator does **authentication** (valid JWT?) at the front door.
- Orchestrator **token-exchanges** (RFC 8693) to a token scoped to the target agent's `aud`.
- Agent does **authorization** itself (aud + role check) via reusable middleware.
- Denials propagate as structured results; UI shows per-agent chips (✅ / 🚫 / ⚠️).
- The browser silently refreshes tokens; exchanged tokens are cached per-subject, so refresh stays in sync.
- Adding an agent = drop a folder + one registry line. No orchestrator code change.

## Components

```
demo-a2a/
├── keycloak/                 # Setup guide + importable realm
├── shared/a2a_auth/          # Reusable: JWT validator, middleware, token-exchanger
├── agents/
│   ├── base_agent.py         # make_agent_app() — one call wires auth + a2a SDK
│   ├── weather_agent/        # role: weather.read
│   └── billing_agent/        # role: billing.read
└── orchestrator/
    ├── main.py               # FastAPI + SSE /chat
    ├── registry.py           # <-- the only place to register a new agent
    ├── router_llm.py         # Azure OpenAI picks which agent + composes reply
    ├── a2a_dispatcher.py     # uses a2a-sdk ClientFactory + bearer httpx hook
    └── static/               # vanilla JS chat UI with oidc-client-ts
```

## Prereqs

- Python 3.11+
- Docker (for Keycloak)
- An Azure OpenAI deployment (endpoint, key, deployment name)

## Setup

### 1. Keycloak

```bash
docker compose up -d keycloak
```

Follow **[keycloak/SETUP.md](keycloak/SETUP.md)** to create the realm, clients, token-exchange permissions, roles, and users. Copy the three client secrets into `.env`.

Alternative: import `keycloak/realm-export.json` via Admin UI → *Create realm → Resource file*, then regenerate the three client secrets and paste them into `.env`. You still have to enable the **token-exchange permission** on each agent client manually (Clients → agent → Permissions tab).

### 2. Environment

```bash
cp .env.example .env
# edit .env: fill KC_*_CLIENT_SECRET + AZURE_OPENAI_*
```

### 3. Python deps

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Unix
pip install -e .
```

## Run

Keycloak already running in Docker. One command starts everything else:

```bash
python run.py
```

Output from all three services is interleaved with colored prefixes. `Ctrl+C` shuts them all down.

To run a subset:

```bash
python run.py weather orchestrator     # skip billing
python run.py orchestrator              # orchestrator only (agents already running elsewhere)
```

Or open separate terminals if you prefer:

```bash
python -m agents.weather_agent.main
python -m agents.billing_agent.main
python -m orchestrator.main
```

Open **http://localhost:3000**. Sign in as:

- **alice / demo** — has only `weather.read`. Asking about billing shows a 🚫 chip + helpful fallback.
- **bob / demo** — has `weather.read` + `billing.read` + `billing.write`. Can hit both agents.

## Demo flow

1. *"what's the weather in Bangalore?"* → 🔵 routing → ✅ `weather` → reply.
2. *"show me my last invoice"* as **alice** → 🚫 `billing — no access: role billing.read` → LLM replies with an apology + suggests weather.
3. Same prompt as **bob** → ✅ `billing` → reply with the fake invoice.
4. Wait 5 minutes; the access token expires. Browser silently renews, cached exchanged tokens invalidate, next call re-exchanges. Seamless.

## Adding a new agent

1. Create Keycloak client (confidential, *Standard token exchange* ON, audience mapper), plus role(s), plus token-exchange permission. See SETUP.md §3–§6.
2. Copy `agents/weather_agent/` → `agents/<name>/`.
3. Edit `card.py` + `executor.py` + `main.py` (change audience and required_roles).
4. Append one `AgentEntry(...)` to `orchestrator/registry.py`.
5. Restart orchestrator. Done.

No auth code, no dispatcher code, no UI code changes.

## Design notes

- **Where each check lives:**
  - Signature + issuer + exp — orchestrator and each agent (defence in depth).
  - `aud` — *only* at the agent (orchestrator shouldn't know per-agent policy).
  - Role — *only* at the agent.
- **No token is forwarded.** Every hop uses RFC 8693 token-exchange; the new token carries the callee's `aud` and the original user `sub`. An attacker who captures one cannot replay it against another agent.
- **SDK usage is idiomatic.** We use `A2ACardResolver`, `ClientFactory`, and `ClientConfig` from `a2a-sdk` — no custom clients. Auth headers are injected via an `httpx.AsyncClient` event hook passed into `ClientConfig(httpx_client=...)`.
- **Refresh sync:** cache key is `(subject_jti, audience)`. A refreshed subject token has a fresh `jti` → exchanged tokens are automatically re-minted, never outlive the subject.
