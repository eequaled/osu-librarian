/* Declarative shortcut registry + `?` help overlay (single source of truth:
 * bindKeyboard() in main.js and this overlay both derive from SHORTCUTS). */
const $ = (id) => document.getElementById(id);

/* {scope, keys, label} — every entry is displayed; the actions stay in
 * main.js (DOM-bound). Keep keys in display order. */
export const SHORTCUTS = [
  { scope: "Search", keys: "/", label: "focus search" },
  { scope: "Search", keys: "Esc", label: "leave search" },
  { scope: "List", keys: "↑ / ↓", label: "move selection" },
  { scope: "List", keys: "space", label: "toggle selection" },
  { scope: "List", keys: "shift+click", label: "select range" },
  { scope: "Global", keys: "?", label: "toggle this help" },
];

export function shortcutsHtml() {
  const scopes = [...new Set(SHORTCUTS.map((s) => s.scope))];
  return scopes.map((scope) => `
    <div class="sc-scope">
      <b>${scope}</b>
      <table class="sc-table">${SHORTCUTS.filter((s) => s.scope === scope)
        .map((s) => `<tr><td><kbd>${s.keys}</kbd></td><td>${s.label}</td></tr>`)
        .join("")}</table>
    </div>`).join("");
}

export function toggleShortcutsDialog(open) {
  const dialog = $("shortcuts-dialog");
  if (!dialog) return false;
  if (open === undefined) open = !dialog.open;
  if (open && !dialog.open) {
    dialog.showModal();
    $("shortcuts-close")?.focus();
  } else if (!open && dialog.open) {
    dialog.close();
  }
  return dialog.open;
}

export function initShortcuts() {
  $("shortcuts-close")?.addEventListener("click", () => toggleShortcutsDialog(false));
  $("shortcuts-dialog")?.addEventListener("close", () => {
    $("shortcuts-dialog").__returnFocus?.();
  });
}
