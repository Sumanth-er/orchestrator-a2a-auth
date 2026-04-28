# Recovery: missing standard scopes after realm import

## Symptom

After importing `realm-export.json`, one or more of these things break:

- Login redirects back with `?error=invalid_scope&error_description=Invalid+scopes:+openid+profile`.
- After login, the username header shows `8e858a90` (a sub prefix) instead of `alice` / `bob`.
- Every agent call fails with `🚫 no access — User '<x>' lacks required role(s) ['weather.read']`, even though the user definitely has the role assigned.

## Root cause

Keycloak's "Create realm → Resource file" import treats the JSON's `clientScopes` array as authoritative. Because `realm-export.json` declares the three custom audience scopes (`aud-orchestrator`, `aud-weather`, `aud-billing`), Keycloak **skips** auto-creating the standard built-in scopes (`profile`, `email`, `roles`, `web-origins`, `acr`, `basic`).

You can confirm: sidebar → **Client scopes** → if the list only shows `aud-*` and `offline_access`, the standards are missing.

Two of those standards are essential for this demo:

| Scope     | What it provides                  | Why we need it                                           |
|-----------|-----------------------------------|----------------------------------------------------------|
| `profile` | `preferred_username` claim        | UI shows the real username; orchestrator logs it        |
| `roles`   | `realm_access.roles` claim        | Agent middleware checks this for `weather.read` etc.    |

## Fix A — manual (3 minutes, no data loss)

### 1. Create the `profile` scope

1. Sidebar → **Client scopes** → **Create client scope**.
2. **Name:** `profile`, **Type:** `Default`, **Protocol:** `openid-connect` → **Save**.
3. **Mappers** tab → **Add mapper → By configuration** → click **User Property**.
4. Fill in:
   - **Name:** `preferred_username`
   - **User Attribute:** `username`
   - **Token Claim Name:** `preferred_username`
   - **Claim JSON Type:** `String`
   - **Add to ID token:** On
   - **Add to access token:** On
   - **Add to userinfo:** On
5. **Save**.

### 2. Create the `roles` scope

1. Sidebar → **Client scopes** → **Create client scope**.
2. **Name:** `roles`, **Type:** `Default`, **Protocol:** `openid-connect` → **Save**.
3. **Mappers** tab → **Add mapper → By configuration** → click **User Realm Role**.
4. Fill in:
   - **Name:** `realm roles`
   - **Realm Role prefix:** *(leave blank)*
   - **Multivalued:** On  ← mandatory, otherwise the role array becomes a single string
   - **Token Claim Name:** `realm_access.roles`  ← the dot creates the nested object the middleware expects
   - **Claim JSON Type:** `String`
   - **Add to ID token:** Off
   - **Add to access token:** On
   - **Add to userinfo:** Off
5. **Save**.

### 3. Assign both scopes to every client

For each of `a2a-ui`, `orchestrator`, `weather-agent`, `billing-agent`:

1. Sidebar → **Clients** → click the client → **Client scopes** tab.
2. **Add client scope** → tick `profile` AND `roles` → **Add → Default**.

### 4. Verify

```bash
curl -s -X POST "http://localhost:8080/realms/a2a-demo/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=a2a-ui" \
  -d "username=alice" \
  -d "password=demo" \
  -d "scope=openid profile"
```

Decode the `access_token` at <https://jwt.io>. You should see:

```json
{
  "preferred_username": "alice",
  "realm_access": { "roles": ["weather.read", "default-roles-a2a-demo", ...] },
  "aud": ["orchestrator", ...]
}
```

If `preferred_username` is there → step 1 worked. If `realm_access.roles` is there → step 2 worked. If `orchestrator` is in `aud` → `aud-orchestrator` was assigned to `a2a-ui` correctly.

### 5. Re-test the app

1. Sign out of the demo, then **Ctrl+F5** the browser tab.
2. Sign in as **alice / demo** → header should show `alice`.
3. Ask *"weather in Bangalore"* → green ✅ chip + a real reply.
4. Ask *"show my invoice"* as alice → red 🚫 chip (`role billing.read`) + apology reply. **This is the demo's whole point** — agent-side authz worked.
5. Sign out, sign in as **bob / demo**, ask the same → green ✅ chip + invoice reply.

## Fix B — wipe and rebuild (clean slate)

If the realm is in an unrecoverable state, easier to start over.

```bash
docker compose down -v          # the -v deletes the keycloak-data volume
docker compose up -d keycloak
```

Wait ~30 s for Keycloak to come up.

Then **don't use "Create realm → Resource file"** (which causes this problem). Instead:

1. Top-left realm dropdown → **Create realm**.
2. **Realm name:** `a2a-demo`. Leave the resource file field empty. → **Create**.
   This auto-creates all standard scopes (`profile`, `email`, `roles`, …).
3. Now layer the customizations on top via partial import:
   - **Realm settings** → **Action** menu (top-right) → **Partial import**.
   - Upload `keycloak/realm-export.json`.
   - Tick **Clients**, **Client scopes**, **Roles**, **Users**.
   - **If any resource exists:** Skip → **Import**.
4. Verify each client (`a2a-ui`, `orchestrator`, `weather-agent`, `billing-agent`) has `profile` and `roles` assigned as Default in its **Client scopes** tab. Add them if not (one click each).

You're now in a known-good state with all standard scopes present and our customizations layered on top.
