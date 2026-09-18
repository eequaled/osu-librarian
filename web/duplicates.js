/* Duplicates view: groups from GET /api/duplicates rendered as collapsible
 * cards; per-row checkboxes feed the normal selection store via hooks. */
const $ = (id) => document.getElementById(id);

export function duplicatesHtml(j) {
  if (!j.group_count) {
    return `<p class="hint">No duplicate mapsets found — every song maps to a single set.</p>`;
  }
  return j.groups.map((g, i) => `
    <details class="dup-group" ${i === 0 ? "open" : ""}>
      <summary>${g.rows[0].artist} — ${g.rows[0].title}
        <span class="hint">(${g.rows.length} diffs across ${new Set(g.rows.map((r) => r.set_id)).size} sets)</span></summary>
      <table class="dup-table">
        ${g.rows.map((r) => `
          <tr>
            <td><input type="checkbox" class="dup-check" data-id="${r.id}"></td>
            <td>[${r.diff}]</td>
            <td>${r.mode_name}</td>
            <td>★${(Number(r.stars) || 0).toFixed(2)}</td>
          </tr>`).join("")}
      </table>
    </details>`).join("");
}

export async function fetchDuplicates() {
  const r = await fetch("/api/duplicates");
  if (!r.ok) throw new Error(`duplicates ${r.status}`);
  return r.json();
}

export function initDuplicates({ isSelectable, toggleRow }) {
  const dialog = $("duplicates-dialog");
  async function paint() {
    let j;
    try {
      j = await fetchDuplicates();
    } catch (e) {
      $("duplicates-body").innerHTML =
        `<p class="hint">cannot reach the server (${e.message}) — close and retry.</p>`;
      return;
    }
    $("duplicates-body").innerHTML = duplicatesHtml(j);
    $("duplicates-body").querySelectorAll(".dup-check").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (isSelectable(cb.dataset.id)) toggleRow({ id: cb.dataset.id });
      });
    });
  }
  $("duplicates-btn")?.addEventListener("click", () => {
    dialog.showModal();
    paint();
  });
  $("duplicates-close")?.addEventListener("click", () => dialog.close());
}
