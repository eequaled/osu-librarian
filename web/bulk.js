/* Bottom bulk bar + export dialog. Selection state lives in main.js. */
import { downloadExport } from "./api.js";
import { toast } from "./views.js";

/* Pure helper so the disabled rule stays testable without DOM. */
export function isExportDisabled(count) {
  return count === 0;
}

export function initBulk(ctx) {
  // ctx: {selectedIds()->string[], selectFiltered(), invertSelection(), clearSelection()}
  const bar = document.getElementById("bulkbar");
  const count = document.getElementById("sel-count");
  const dialog = document.getElementById("export-dialog");
  const fmtBtns = [...dialog.querySelectorAll("[data-fmt]")];
  fmtBtns.forEach((b) => { b.disabled = true; });
  let exportTrigger = null;

  document.getElementById("sel-filtered").addEventListener("click", () => ctx.selectFiltered());
  document.getElementById("sel-invert").addEventListener("click", () => ctx.invertSelection());
  document.getElementById("sel-clear").addEventListener("click", () => ctx.clearSelection());
  document.getElementById("sel-copy").addEventListener("click", async () => {
    const ids = ctx.selectedIds();
    try {
      await navigator.clipboard.writeText(ids.join("\n"));
      toast(`${ids.length} ids copied`);
    } catch {
      toast("clipboard blocked by the browser", "error");
    }
  });
  document.getElementById("sel-export").addEventListener("click", () => {
    const n = ctx.selectedIds().length;
    exportTrigger = document.activeElement;
    document.getElementById("export-count").textContent = n;
    fmtBtns.forEach((b) => { b.disabled = isExportDisabled(n); });
    dialog.showModal();
    // Focus first control on open; the dialog's close handler restores focus.
    (fmtBtns.find((b) => !b.disabled) || document.getElementById("export-close"))?.focus();
  });
  document.getElementById("export-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { exportTrigger?.focus?.(); });
  dialog.querySelectorAll("[data-fmt]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ids = ctx.selectedIds();
      if (ids.length === 0) {
        toast("nothing selected", "error");
        return;
      }
      try {
        await downloadExport(ids, btn.dataset.fmt);
        dialog.close();
        toast(`exported ${ids.length} beatmaps`);
      } catch (e) {
        toast(`export failed: ${e.message}`, "error");
      }
    });
  });

  return {
    update(n) {
      bar.hidden = n === 0;
      count.textContent = `${n} selected`;
      fmtBtns.forEach((b) => { b.disabled = isExportDisabled(n); });
    },
  };
}
