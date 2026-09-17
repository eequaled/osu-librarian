/* Account linking dialog: one-click OAuth with polling. Uses api.js for the
 * authorize URL + status refresh; reports back via onLinked callback. */
import { authUrl, status } from "./api.js";
import { toast } from "./views.js";

/* Relay one-click linking URL format (frontend-built, no server endpoint):
 * https://osu.ppy.sh/oauth/authorize?client_id=<RELAY_CLIENT_ID>
 *   &redirect_uri=<RELAY_URL>/auth/callback&response_type=code
 *   &scope=identify+public&state=<ticket>.<localport>
 * <ticket> comes from POST {relay_url}/pair; <localport> is location.port.
 * The relay redirects the browser to
 * http://127.0.0.1:<localport>/api/auth/relay-return?ticket=<ticket>. */
export function buildRelayAuthorizeUrl(relayUrl, relayClientId, ticket, port) {
  const redirect = `${String(relayUrl).replace(/\/+$/, "")}/auth/callback`;
  return "https://osu.ppy.sh/oauth/authorize"
    + `?client_id=${encodeURIComponent(relayClientId)}`
    + `&redirect_uri=${encodeURIComponent(redirect)}`
    + "&response_type=code"
    + "&scope=identify+public"
    + `&state=${encodeURIComponent(`${ticket}.${port}`)}`;
}

async function fetchAuthStatus() {
  const r = await fetch("/api/auth/status");
  if (!r.ok) throw new Error(`status ${r.status}`);
  return r.json();
}

async function saveAuthConfig(clientId, clientSecret) {
  const r = await fetch("/api/auth/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `config ${r.status}`);
  return data;
}

export function initAuth(onLinked) {
  const dialog = document.getElementById("auth-dialog");
  const clientIdInput = document.getElementById("auth-client-id");
  const clientSecretInput = document.getElementById("auth-client-secret");
  const saveBtn = document.getElementById("auth-save");
  const authorizeBtn = document.getElementById("auth-open");
  const copyBtn = document.getElementById("auth-copy");
  const callbackInput = document.getElementById("auth-callback");
  const relayBtn = document.getElementById("auth-relay-open");
  const relayHint = document.getElementById("auth-relay-hint");
  let pollTimer = 0;
  let relayUrl = "";
  let relayClientId = 0;

  const callbackUrl = () => callbackInput.value;

  const stopPolling = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = 0;
    }
  };

  async function refreshStatus() {
    let st = null;
    try {
      st = await fetchAuthStatus();
    } catch {
      authorizeBtn.disabled = true;
      if (relayBtn) relayBtn.disabled = true;
      return null;
    }
    authorizeBtn.disabled = !st.configured;
    // One-click section: enabled only when the relay is configured server-side.
    relayUrl = st?.relay?.url || "";
    relayClientId = st?.relay?.client_id || 0;
    const available = !!(st?.relay?.available);
    if (relayBtn) relayBtn.disabled = !available;
    if (relayHint) {
      relayHint.textContent = available
        ? ""
        : "one-click linking is not available on this server — use your own app below.";
    }
    // Show the server's actual callback URL (correct on any port/config).
    if (st.redirect_uri) callbackInput.value = st.redirect_uri;
    return st;
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(async () => {
      let st = null;
      try {
        st = await fetchAuthStatus();
      } catch {
        return;
      }
      if (st?.linked) {
        stopPolling();
        dialog.close();
        const who = st.username
          ? `${st.username} (${st.user_id})`
          : (st.user_id ? `user ${st.user_id}` : "account linked");
        toast(`linked as ${who}`);
        onLinked?.(await status().catch(() => st));
      }
    }, 2000);
  }

  document.getElementById("link-btn").addEventListener("click", async () => {
    clientSecretInput.value = "";
    dialog.showModal();
    await refreshStatus();
    startPolling();
  });
  dialog.addEventListener("close", stopPolling);
  document.getElementById("auth-close").addEventListener("click", () => dialog.close());

  if (relayBtn) {
    relayBtn.addEventListener("click", async () => {
      let ticket = "";
      try {
        const r = await fetch(`${String(relayUrl).replace(/\/+$/, "")}/pair`, {
          method: "POST",
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok || !data.ticket) throw new Error(data.error || `pair ${r.status}`);
        ticket = data.ticket;
      } catch {
        toast("linking service unreachable — use your own app below", "error");
        return;
      }
      const port = window.location.port || "";
      window.open(
        buildRelayAuthorizeUrl(relayUrl, relayClientId, ticket, port),
        "_blank",
        "noopener",
      );
      startPolling();
    });
  }

  saveBtn.addEventListener("click", async () => {
    const clientId = clientIdInput.value.trim();
    const clientSecret = clientSecretInput.value.trim();
    if (!clientId || !clientSecret) {
      toast("enter client id and secret first", "error");
      return;
    }
    try {
      await saveAuthConfig(clientId, clientSecret);
      toast("credentials saved");
      await refreshStatus();
    } catch (e) {
      toast(`save failed: ${e.message}`, "error");
    }
  });

  authorizeBtn.addEventListener("click", async () => {
    try {
      window.open(await authUrl(), "_blank", "noopener");
    } catch (e) {
      toast(`cannot get authorize URL: ${e.message} (credentials saved?)`, "error");
    }
  });

  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(callbackUrl());
      toast("callback URL copied");
    } catch {
      toast(callbackUrl());
    }
  });
}

export function renderAccountChip(auth) {
  const chip = document.getElementById("account-chip");
  if (auth?.linked) {
    chip.textContent = auth.user_id ? `linked · ${auth.user_id}` : "linked";
    chip.classList.add("linked");
  } else {
    chip.textContent = "not linked";
    chip.classList.remove("linked");
  }
}
