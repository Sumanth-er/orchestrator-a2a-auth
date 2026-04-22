# Keycloak 25.5.5 Setup Guide

This guide walks through a clean Keycloak 25.5.5 realm setup for the A2A demo, using the **new Admin UI**. Every step uses breadcrumbs you can follow in the sidebar.

## 0. Start Keycloak

```bash
docker compose up -d keycloak
```

Wait ~30 s, then open **http://localhost:8080** → **Administration Console**. Log in as `admin / admin`.

> The `--features=token-exchange-standard` flag in `docker-compose.yml` enables RFC 8693 standard token exchange (a preview feature in 25.5.5).

---

## 1. Create the realm

1. Top-left dropdown (shows **Keycloak master**) → **Create realm**.
2. **Realm name:** `a2a-demo` → **Create**.
3. You should now be inside the `a2a-demo` realm (dropdown shows it).

---

## 2. Create the UI client (public, PKCE)

Sidebar → **Clients** → **Create client**.

**General settings:**
- Client type: `OpenID Connect`
- Client ID: `a2a-ui`
- Name: `A2A Demo UI`
- → **Next**

**Capability config:**
- Client authentication: **OFF** (public client)
- Authorization: **OFF**
- Authentication flow: check only **Standard flow** (nothing else)
- → **Next**

**Login settings:**
- Root URL: `http://localhost:3000`
- Home URL: `http://localhost:3000`
- Valid redirect URIs: `http://localhost:3000/*`
- Valid post-logout redirect URIs: `http://localhost:3000/*`
- Web origins: `http://localhost:3000`
- → **Save**

**Advanced tab (scroll down):**
- Proof Key for Code Exchange Code Challenge Method: `S256`
- → **Save**

---

## 3. Create confidential clients for orchestrator + agents

Repeat the following for each of: `orchestrator`, `weather-agent`, `billing-agent`.

Sidebar → **Clients** → **Create client**.

**General settings:**
- Client type: `OpenID Connect`
- Client ID: *(one of the three names above)*
- → **Next**

**Capability config:**
- Client authentication: **ON**
- Authorization: **OFF**
- Authentication flow: check **Service accounts roles** and **Standard token exchange** *(this is the RFC 8693 toggle — only visible when the `token-exchange-standard` feature is enabled)*
- → **Next**

**Login settings:**
- Leave all blank / defaults → **Save**

**Credentials tab:**
- Copy the **Client Secret**. Paste it into `.env`:
  - `orchestrator` → `KC_ORCHESTRATOR_CLIENT_SECRET`
  - `weather-agent` → `KC_WEATHER_CLIENT_SECRET`
  - `billing-agent` → `KC_BILLING_CLIENT_SECRET`

---

## 4. Audience mappers (so agent tokens carry `aud`)

For each agent client (`weather-agent`, `billing-agent`):

1. Clients → click the agent client → **Client scopes** tab.
2. Click the row that ends in `-dedicated` (e.g. `weather-agent-dedicated`).
3. Tab **Mappers** → **Add mapper** → **By configuration** → **Audience**.
4. Fill:
   - Name: `aud-self`
   - Included Client Audience: *(the client itself — e.g. `weather-agent`)*
   - Add to access token: **ON**
5. → **Save**.

This ensures tokens exchanged *for* this agent include `aud: weather-agent` (or `billing-agent`), which the agent middleware verifies.

---

## 5. Token-exchange permissions (who may exchange to whom)

For **each target agent client** (`weather-agent`, `billing-agent`):

1. Clients → click the agent client → **Permissions** tab.
2. Set **Permissions enabled** to **ON** (creates a hidden `realm-management` authorization).
3. Click the **token-exchange** permission row.
4. In **Policies**, click **Create policy** → **Client policy**:
   - Name: `orchestrator-can-exchange` (or `weather-can-exchange` when granting weather → billing)
   - Clients: select `orchestrator` (add `weather-agent` too on the billing permission, so weather may call billing)
   - → **Save**
5. Back on the permission, add the new policy to the **Policies** field → **Save**.

Result: only clients you list here can swap a token *for* this agent's audience. Anyone else gets `403` from Keycloak at exchange time.

---

## 6. Realm roles (per-agent authorization)

Sidebar → **Realm roles** → **Create role**:

- `weather.read`
- `billing.read`
- `billing.write`

---

## 7. Users

Sidebar → **Users** → **Add user** (do this twice):

| Username | Email | First | Last |
|----------|-------|-------|------|
| `alice` | alice@example.com | Alice | A. |
| `bob` | bob@example.com | Bob | B. |

For each:
1. **Credentials** tab → **Set password** → set a password (e.g. `demo`), **Temporary: OFF** → **Save password**.
2. **Role mapping** tab → **Assign role** → filter "Realm roles":
   - `alice` → add `weather.read`
   - `bob` → add `weather.read`, `billing.read`, `billing.write`

---

## 8. Token lifetime (short, so refresh is demo-able)

Sidebar → **Realm settings** → **Tokens** tab:
- Access Token Lifespan: `5 Minutes`
- → **Save**

---

## 9. Smoke test

Get a token as `alice` with the UI client:

```bash
curl -s -X POST "http://localhost:8080/realms/a2a-demo/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=a2a-ui" \
  -d "username=alice" \
  -d "password=demo" \
  -d "scope=openid" | jq
```

(Password grant works here only because no secret is set on `a2a-ui`. In the actual UI we use PKCE.)

Decode the `access_token` at https://jwt.io — you should see `realm_access.roles: ["weather.read", ...]`.

Test a token exchange from orchestrator → weather-agent:

```bash
USER_TOKEN=...   # the access_token above
curl -s -X POST "http://localhost:8080/realms/a2a-demo/protocol/openid-connect/token" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "client_id=orchestrator" \
  -d "client_secret=$KC_ORCHESTRATOR_CLIENT_SECRET" \
  -d "subject_token=$USER_TOKEN" \
  -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "requested_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "audience=weather-agent" | jq
```

Decode the new token — `aud` should be `weather-agent`, `sub` still alice's user id.

---

## Troubleshooting

- **`token-exchange-standard` toggle not visible** on Capability config → feature flag not enabled; recheck container command line.
- **Exchange returns 403 `not_allowed`** → permission on target client missing (step 5).
- **`aud` missing from exchanged token** → audience mapper missing on target client (step 4).
- **After-restart login broken** → delete `./keycloak-data` volume and redo this guide, or import `realm-export.json`.
