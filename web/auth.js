/* Account linking dialog: one-click OAuth with polling. Uses api.js for the
 * authorize URL + status refresh; reports back via onLinked callback. */
import { authUrl, status } from "./api.js";
import { toast } from "./views.js";

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
  let pollTimer = 0;

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
      return null;
    }
    authorizeBtn.disabled = !st.configured;
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
