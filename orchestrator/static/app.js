// A2A Demo UI — PKCE login, silent refresh, SSE chat with per-agent chips.
//
// Auth:
//   - OIDC PKCE via oidc-client-ts. Access-token lifespan ~5 min; library
//     auto-renews silently ~60s before expiry.
//   - Every outbound fetch reads getUser() fresh (not a cached token) so the
//     orchestrator never sees a stale token.
//
// SSE:
//   - POST /chat would be ideal but EventSource is GET-only. We use fetch()
//     + ReadableStream to read text/event-stream — this lets us POST the body
//     and include the Authorization header.

const log = document.getElementById("log");
const gate = document.getElementById("gate");
const chat = document.getElementById("chat");
const whoEl = document.getElementById("who");
const logoutBtn = document.getElementById("logout");
const loginBtn = document.getElementById("login");
const composer = document.getElementById("composer");
const msgInput = document.getElementById("msg");

let userManager = null;

async function init() {
  const cfg = await (await fetch("/api/config")).json();
  userManager = new oidc.UserManager({
    authority: `${cfg.kc_url}/realms/${cfg.realm}`,
    client_id: cfg.client_id,
    redirect_uri: window.location.origin + "/",
    post_logout_redirect_uri: window.location.origin + "/",
    response_type: "code",
    scope: "openid profile",
    automaticSilentRenew: true,
    accessTokenExpiringNotificationTimeInSeconds: 60,
    loadUserInfo: false,
  });

  userManager.events.addAccessTokenExpired(() => signIn());
  userManager.events.addSilentRenewError(err => console.warn("silent renew failed", err));

  // Handle the OIDC callback (?code=..&state=..).
  if (window.location.search.includes("code=")) {
    try {
      await userManager.signinRedirectCallback();
      window.history.replaceState({}, document.title, window.location.pathname);
    } catch (e) {
      console.error("signin callback failed", e);
    }
  }

  const user = await userManager.getUser();
  if (user && !user.expired) enterChat(user);
  else showGate();
}

function showGate() {
  gate.hidden = false;
  chat.hidden = true;
  logoutBtn.hidden = true;
  whoEl.textContent = "—";
}

async function enterChat(user) {
  gate.hidden = true;
  chat.hidden = false;
  logoutBtn.hidden = false;
  whoEl.textContent = user.profile?.preferred_username
    || user.profile?.sub?.slice(0, 8) || "signed in";
  msgInput.focus();
}

async function signIn() { await userManager.signinRedirect(); }
async function signOut() {
  await userManager.signoutRedirect().catch(() => userManager.removeUser());
}

async function currentToken() {
  const user = await userManager.getUser();
  if (!user || user.expired) {
    try { await userManager.signinSilent(); }
    catch { signIn(); throw new Error("auth-required"); }
    return (await userManager.getUser()).access_token;
  }
  return user.access_token;
}

// --- UI builders --------------------------------------------------

function addBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function addChipRow() {
  const row = document.createElement("div");
  row.className = "chips";
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return row;
}

function addChip(row, { agent, status, reason, elapsed_ms }) {
  const chip = document.createElement("span");
  chip.className = `chip ${status}`;
  chip.dataset.agent = agent;

  const label = {
    pending: "routing",
    ok: "authorized",
    denied: "no access",
    error: "error",
  }[status] || status;

  const marker = status === "pending"
    ? `<span class="spinner"></span>`
    : `<span class="dot"></span>`;

  chip.innerHTML =
    `${marker}<strong>${agent}</strong>` +
    `<span class="reason">· ${label}${reason ? ": " + escapeHtml(reason) : ""}</span>` +
    (elapsed_ms ? `<span class="time">${elapsed_ms}ms</span>` : "");
  row.appendChild(chip);
  return chip;
}

function updateChip(row, agent, patch) {
  const existing = row.querySelector(`.chip[data-agent="${CSS.escape(agent)}"]`);
  if (existing) existing.remove();
  return addChip(row, { agent, ...patch });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// --- SSE chat -----------------------------------------------------

async function sendMessage(text) {
  addBubble("user", text);
  const chipRow = addChipRow();

  let token;
  try { token = await currentToken(); }
  catch { return; }

  const resp = await fetch("/chat", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
    },
    body: JSON.stringify({ message: text }),
  });

  if (resp.status === 401) {
    // Token stale — refresh once and retry.
    try { await userManager.signinSilent(); return sendMessage(text); }
    catch { signIn(); return; }
  }
  if (!resp.ok || !resp.body) {
    addBubble("assistant", `⚠️ ${resp.status} ${resp.statusText}`);
    return;
  }

  await parseSse(resp.body, ({ event, data }) => {
    try { data = JSON.parse(data); } catch { /* raw */ }
    if (event === "agent_selected") {
      addChip(chipRow, { agent: data.agent, status: "pending" });
    } else if (event === "agent_result") {
      updateChip(chipRow, data.agent, {
        status: data.status,
        reason: data.reason,
        elapsed_ms: data.elapsed_ms,
      });
    } else if (event === "no_agent") {
      addChip(chipRow, {
        agent: "direct", status: "ok", reason: data.rationale || "answered directly",
      });
    } else if (event === "reply") {
      addBubble("assistant", data.text);
    } else if (event === "user_authenticated") {
      // no-op for now
    }
  });
}

async function parseSse(stream, onEvent) {
  const reader = stream.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = { event: "message", data: "" };
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) ev.event = line.slice(6).trim();
        else if (line.startsWith("data:")) ev.data += line.slice(5).trim();
      }
      if (ev.data || ev.event !== "message") onEvent(ev);
    }
  }
}

// --- Wire up ------------------------------------------------------

loginBtn.addEventListener("click", signIn);
logoutBtn.addEventListener("click", signOut);
composer.addEventListener("submit", e => {
  e.preventDefault();
  const t = msgInput.value.trim();
  if (!t) return;
  msgInput.value = "";
  sendMessage(t);
});

init().catch(e => {
  console.error(e);
  showGate();
});
