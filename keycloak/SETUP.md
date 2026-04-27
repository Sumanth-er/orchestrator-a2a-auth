# Keycloak 25.5.5 Setup Guide

This guide walks through a clean Keycloak 25.5.5 realm setup for the A2A demo, using the **new Admin UI**. Every step uses breadcrumbs you can follow in the sidebar.

## 0. Start Keycloak

```bash
docker compose up -d keycloak
```

Wait ~30 s, then open **http://localhost:8080** → **Administration Console**. Log in as `admin / admin`.

> The `--features=token-exchange-standard` flag in `docker-compose.yml` enables RFC 8693 standard token exchange (a preview feature in 25.5.5).

---

## Quick path — import realm-export.json

If you just want a working demo and trust the bundled secrets, skip §1–§8 entirely:

1. After step 0 above, in the top-left realm dropdown click **Create realm**.
2. **Resource file** → upload **`keycloak/realm-export.json`** → **Create**.
3. Done. Everything below (clients, scopes, roles, users, secrets) is preconfigured exactly as §1–§8 describe.

The bundled client secrets in `realm-export.json` already match the values in `.env.example`, so `cp .env.example .env` plus your Azure OpenAI key is the only env work needed. Skip ahead to **§9 Smoke test**.

If you'd rather click through everything by hand to learn how it fits together, do §1–§8 below instead.

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
- Authentication flow: check **Standard flow** AND **Direct access grants**
  *(Direct access grants is needed only so we can curl-test password flow in §9. The real UI uses PKCE — you can disable it again after testing.)*
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

## 4. Audience scopes — the chain

> **The Standard Token Exchange rule (RFC 8693 in Keycloak 25/26):**
> a client may exchange a subject token only if **the calling client is in the subject token's `aud` claim**. There are no fine-grained admin permissions to grant — the audience claim *is* the authorisation. So the audience chain must be:
>
> `user-token (aud=orchestrator) → exchanged (aud=weather-agent) → exchanged (aud=billing-agent) …`
>
> This means we need **three** audience scopes — one per "next hop" — assigned to whoever needs to make that hop.

### 4a. Create three shared client scopes

For each name in `aud-orchestrator`, `aud-weather`, `aud-billing`:

1. Sidebar → **Client scopes** → **Create client scope**:
   - Name: *(one of the three)*
   - Type: `None`
   - Protocol: `openid-connect`
   - → **Save**
2. Open the new scope → **Mappers** tab → **Add mapper** → **By configuration** → **Audience**:
   - Name: same as the scope (e.g. `aud-weather`)
   - Included Client Audience: the corresponding client (`orchestrator` / `weather-agent` / `billing-agent`)
   - Add to access token: **ON**
   - Add to ID token: OFF
   - → **Save**

### 4b. Assign each scope to the clients that need it

| Client          | Default scope to add | Why                                                        |
|-----------------|----------------------|------------------------------------------------------------|
| `a2a-ui`        | `aud-orchestrator`   | User tokens must include orchestrator so it can exchange.  |
| `orchestrator`  | `aud-weather`, `aud-billing` | So exchanged tokens carry the right agent audience.|
| `weather-agent` | `aud-billing`        | Lets weather call billing in the agent-to-agent demo.      |
| `billing-agent` | *(none)*             | Leaf — never exchanges further.                            |

For each row: Clients → *client* → **Client scopes** tab → **Add client scope** → tick → **Add → Default**.

After this, the chain works end to end: alice's UI token already lists orchestrator as audience, the exchange to weather succeeds because orchestrator is allowed, and so on down.

---

## 5. (Skipped — fine-grained permissions are not used)

Earlier Keycloak versions required granting `token-exchange` permission on each target client via the **Permissions** tab. **Standard Token Exchange does not use that mechanism.** As long as §4 is set up correctly, no permission grants are needed. The only authorisation rule is "calling client must be in the subject token's `aud`" (which §4 ensures).

If you accidentally enabled fine-grained permissions on any client during setup, it's harmless — just leaves an unused authorisation resource around.

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

> Requires **Direct access grants** on `a2a-ui` (set in §2). Disable it after testing if you want strict PKCE-only.

Get a user token as `alice`:

```bash
curl -s -X POST "http://localhost:8080/realms/a2a-demo/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=a2a-ui" \
  -d "username=alice" \
  -d "password=demo" \
  -d "scope=openid" | jq -r .access_token
```

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

Decode the new token — `aud` should include `weather-agent`, `sub` still alice's user id.

---

## Troubleshooting

- **`token-exchange-standard` toggle not visible** on Capability config → feature flag not enabled; recheck container command line in `docker-compose.yml`.
- **`invalid_grant: unsupported_grant_type`** at the password endpoint → Direct access grants is OFF on `a2a-ui` (turn it on in Capability config).
- **`invalid_client: Client is not the audience of the subject_token`** during token exchange → the user token doesn't include the caller in its `aud`. Add `aud-orchestrator` (or `aud-<caller>`) as a Default client scope on the issuer of the subject token. See §4 — this is the most common mistake.
- **`invalid_request: client audience not available`** during exchange → the `aud-<target>` scope is missing or not assigned to the caller as Default. Re-check §4b.
- **`aud` claim missing from exchanged token** → audience mapper inside the scope doesn't have "Add to access token" enabled (§4 step 2).
- **Agent returns 401 "Token audience mismatch"** → exchanged token doesn't contain the agent's client id. Either §4 mapper config is wrong, or the scope is **Optional** instead of **Default**.
- **Agent returns 403 "lacks required role"** → the user is missing the realm role the agent requires (§6/§7). Working as designed.
- **After-restart login broken** → delete `./keycloak-data` volume and re-import `realm-export.json`.
