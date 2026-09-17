/* Bottom bulk bar + export dialog. Selection state lives in main.js. */
import { downloadExport } from "./api.js";
import { toast } from "./views.js";

export function initBulk(ctx) {
  // ctx: {selectedIds()->string[], selectFiltered(), invertSelection(), clearSelection()}
  const bar = document.getElementById("bulkbar");
  const count = document.getElementById("sel-count");
  const dialog = document.getElementById("export-dialog");

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
    document.getElementById("export-count").textContent = ctx.selectedIds().length;
    dialog.showModal();
  });
  document.getElementById("export-close").addEventListener("click", () => dialog.close());
  dialog.querySelectorAll("[data-fmt]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ids = ctx.selectedIds();
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
    },
  };
}
