/* Reconciliation panel: local vs online played buckets + lazer-style math.
 * Data: GET /api/reconciliation. Bucket clicks drive the played filter via
 * the hooks below (main.js owns filter state). No imports keep this module
 * dependency-light; api.js functions are injected for testability. */
const $ = (id) => document.getElementById(id);

function bucketRow(id, label, value, hint) {
  return `<button class="recon-bucket" id="${id}" data-value="${hint}">
    <span class="recon-label">${label}</span>
    <span class="recon-value">${value}</span></button>`;
}

export function reconcileHtml(j, lazerValue = "") {
  if (!j.available) {
    return `<p>No library loaded — run a scan first.</p>`;
  }
  const checking = j.checking
    ? `<p class="hint" id="recon-progress" role="status">online check: ` +
      `${j.checking.state} · ${j.checking.done}/${j.checking.total || "?"}…</p>`
    : "";
  const cta = j.linked
    ? (j.checking ? "" : `<button id="recon-check">run online check</button>`)
    : `<button id="recon-link">link account first</button>`;
  return `
    <table class="recon-grid" aria-label="played reconciliation">
      <tr><td>total difficulties</td><td>${j.total}</td></tr>
      <tr><td>local played</td><td>${bucketRow("recon-local", "local played", j.local_played, "played")}</td></tr>
      <tr><td>online-only played</td><td>${bucketRow("recon-online", "online-only", j.online_only, "online_only")}</td></tr>
      <tr><td>unplayed</td><td>${bucketRow("recon-unplayed", "unplayed", j.unplayed, "unplayed")}</td></tr>
      <tr class="recon-formula"><td>lazer-style unplayed<br><span class="hint">= unplayed + online-only</span></td><td>${j.lazer_style_unplayed}</td></tr>
    </table>
    ${checking}${cta}
    <label class="recon-lazer">lazer "by rank" unplayed (from lazer):
      <input id="recon-lazer-count" type="number" min="0" inputmode="numeric"
             placeholder="e.g. 3025" value="${lazerValue ?? ""}"></label>
    <div id="recon-lazer-verdict" aria-live="polite"></div>
    <p class="hint">lazer "by rank" counts online-played maps as played; paste
    lazer's own count above and the panel says exactly what explains the gap.</p>`;
}

/* Pure verdict builder for the lazer-count comparison (R2.2). Returns null
 * until a number is entered; otherwise states the match/mismatch explicitly
 * and accounts for every map in the gap. */
export function lazerVerdictHtml(j, lazerCount) {
  if (!Number.isFinite(lazerCount)) return null;
  const mine = j.lazer_style_unplayed;
  if (lazerCount === mine) {
    return `<p class="recon-match">✔ lazer's ${lazerCount} unplayed match the ` +
      `lazer-style count exactly — the gap is fully explained by ` +
      `${j.online_only} online-only played maps.</p>`;
  }
  const diff = mine - lazerCount;
  const word = diff > 0 ? "more" : "fewer";
  const n = Math.abs(diff);
  const why = diff > 0
    ? `these ${n} maps are played in lazer but have no visible online score ` +
      `(unranked/submitted-only plays, or scores outside the fetched window)`
    : `our online check found ${n} online-only played maps that lazer's own ` +
      `"by rank" view does not count (or the entered number is stale)`;
  return `<p class="recon-mismatch">✘ does not match: lazer says ${lazerCount}, ` +
      `this tool derives ${mine} — ${n} ${word}. ${why}.</p>`;
}

export async function fetchReconciliation(baseUrl = "") {
  const r = await fetch(`${baseUrl}/api/reconciliation`);
  if (!r.ok) throw new Error(`reconciliation ${r.status}`);
  return r.json();
}

export function initReconcile(hooks) {
  const { getPlayed, setPlayedFilter, startOnlineCheck } = hooks;
  const dialog = $("reconcile-dialog");
  let lazerValue = "";

  async function paint() {
    let j;
    try {
      j = await fetchReconciliation();
    } catch (e) {
      $("reconcile-body").innerHTML =
        `<p class="hint">cannot reach the server (${e.message}) — close and retry.</p>`;
      return;
    }
    $("reconcile-body").innerHTML = reconcileHtml(j, lazerValue);
    const active = getPlayed();
    for (const id of ["recon-local", "recon-online", "recon-unplayed"]) {
      const b = $(id);
      if (!b) continue;
      b.classList.toggle("active", b.dataset.value === active);
      // Clicking the active bucket clears back to "all"; the previous filter
      // state is the main-list filter itself, so "all" restores it exactly.
      b.addEventListener("click", () => {
        setPlayedFilter(b.dataset.value === active ? "all" : b.dataset.value);
        paint();
      });
    }
    if (j.checking && j.checking.state !== "done" && j.checking.state !== "error") {
      setTimeout(() => { if (dialog.open) paint(); }, 1500);
    }
    const lazerInput = $("recon-lazer-count");
    if (lazerInput) {
      const verdict = $("recon-lazer-verdict");
      const updateVerdict = () => {
        const raw = lazerInput.value.trim();
        lazerValue = raw;
        const n = raw === "" ? NaN : Number(raw);
        verdict.innerHTML = lazerVerdictHtml(j, n) || "";
      };
      lazerInput.addEventListener("input", updateVerdict);
      updateVerdict();
    }
    const checkBtn = $("recon-check");
    if (checkBtn) checkBtn.addEventListener("click", async () => {
      checkBtn.disabled = true;
      try {
        await startOnlineCheck();
        paint();
      } catch (e) {
        $("reconcile-body").insertAdjacentHTML("beforeend",
          `<p class="hint">online check failed: ${e.message}</p>`);
        checkBtn.disabled = false;
      }
    });
    const linkBtn = $("recon-link");
    if (linkBtn) linkBtn.addEventListener("click", () => {
      dialog.close();
      $("link-btn")?.click();
    });
  }

  $("reconcile-btn")?.addEventListener("click", () => {
    dialog.showModal();
    paint();
  });
}
