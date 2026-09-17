/* Account linking dialog. Uses api.js; reports back via onLinked callback. */
import { authUrl, status, submitCode } from "./api.js";
import { toast } from "./views.js";

export function initAuth(onLinked) {
  const dialog = document.getElementById("auth-dialog");
  const codeInput = document.getElementById("auth-code");

  document.getElementById("link-btn").addEventListener("click", () => {
    codeInput.value = "";
    dialog.showModal();
  });
  document.getElementById("auth-close").addEventListener("click", () => dialog.close());

  document.getElementById("auth-open").addEventListener("click", async () => {
    try {
      window.open(await authUrl(), "_blank", "noopener");
    } catch (e) {
      toast(`cannot get authorize URL: ${e.message} (is client_id set?)`, "error");
    }
  });

  document.getElementById("auth-submit").addEventListener("click", async () => {
    const code = codeInput.value.trim();
    if (!code) return;
    try {
      const res = await submitCode(code);
      dialog.close();
      toast(res.username ? `linked as ${res.username}` : "account linked");
      onLinked?.(await status());
    } catch (e) {
      toast(`link failed: ${e.message}`, "error");
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
