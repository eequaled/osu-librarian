/* Rendering: stats, windowed list, detail pane, toasts. Event wiring lives in main.js. */
import { escapeHtml, isPlayed } from "./store.js";

const OVERSCAN = 12;

export const MAX_TOASTS = 3;

export function toast(msg, kind = "") {
  const box = document.getElementById("toasts");
  if (!box) return;
  while (box.children.length >= MAX_TOASTS) box.firstChild.remove();
  const el = document.createElement("div");
  el.className = `toast ${kind}`.trim();
  if (kind === "error") el.setAttribute("role", "alert");
  const text = document.createElement("span");
  text.textContent = msg;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "toast-close";
  btn.setAttribute("aria-label", "dismiss notification");
  btn.textContent = "×";
  btn.addEventListener("click", () => el.remove());
  el.append(text, btn);
  box.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

export function renderStats(maps, summary, mode) {
  const split = summary.onlineOnly
    ? ` (<b>${summary.local}</b> local + <b>${summary.onlineOnly}</b> online)`
    : "";
  document.getElementById("stats").innerHTML =
    `mode: <b>${escapeHtml(mode)}</b><br>` +
    `sets: <b>${summary.sets}</b><br>` +
    `diffs: <b>${summary.diffs}</b><br>` +
    `played: <b>${summary.played}</b>${split}<br>unplayed: <b>${summary.unplayed}</b>`;
}

function mapMeta(m) {
  return `${escapeHtml(m.creator || "")} · ★${(m.stars || 0).toFixed(2)} · ` +
    `${escapeHtml(m.mode_name || "")} · ${escapeHtml(m.ranked || "")}` +
    (m.grade ? ` · <span class="grade">${escapeHtml(m.grade)}</span>` : "");
}

/* Stable DOM id for a virtual-list row, used for aria-activedescendant. */
export function rowDomId(i) {
  return `list-row-${i}`;
}

function rowHtml(row, selected) {
  const sel = selected ? "☑" : "☐";
  if (row.type === "set") {
    const s = row.set;
    const firstId = s.maps[0]?.id;
    const thumb = firstId
      ? `<img class="thumb" loading="lazy" src="/api/art?id=${encodeURIComponent(firstId)}" alt="" onerror="this.remove()">`
      : "";
    return `<div class="cb" aria-hidden="true">${sel}</div>` + thumb +
      `<div class="main-cell">▸ ${escapeHtml(s.artist)} — ${escapeHtml(s.title)} ` +
      `<span class="sub">${s.maps.length} diffs · ${s.played}/${s.maps.length} played · ★${s.maxStars.toFixed(1)}</span></div>`;
  }
  const m = row.map;
  const dot = isPlayed(m) ? "yes" : "";
  const pad = row.child ? "style='padding-left:28px'" : "";
  const thumb = m.id
    ? `<img class="thumb" loading="lazy" src="/api/art?id=${encodeURIComponent(m.id)}" alt="" onerror="this.remove()">`
    : "";
  return `<div class="cb" aria-hidden="true">${sel}</div><div class="dot ${dot}"></div>` + thumb +
    `<div class="main-cell" ${pad}>${escapeHtml(m.artist)} — ${escapeHtml(m.title)} ` +
    `<span class="sub">[${escapeHtml(m.diff)}]</span><br><span class="sub">${mapMeta(m)}</span></div>` +
    `<div class="meta"><span>${escapeHtml(m.mode_name || "")}</span>` +
    `<span>★${(m.stars || 0).toFixed(2)}</span>` +
    `<span class="grade">${escapeHtml(m.grade || "—")}</span></div>`;
}

/** Windowed list: only visible rows (+overscan) are in the DOM. */
export class ListView {
  constructor(callbacks) {
    this.list = document.getElementById("list");
    this.spacer = document.getElementById("list-spacer");
    this.rowsEl = document.getElementById("list-rows");
    this.cb = callbacks; // {isSelected(row)->bool, isActive(row)->bool, onRowClick(row, ev)}    this.rows = [];
    this.offsets = [0];
    this.raf = 0;
    this.list.addEventListener("scroll", () => {
      cancelAnimationFrame(this.raf);
      this.raf = requestAnimationFrame(() => this.render());
    });
    this.rowsEl.addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-idx]");
      if (el) this.cb.onRowClick(this.rows[+el.dataset.idx], ev);
    });
  }

  setRows(rows) {
    this.rows = rows;
    this.offsets = [0];
    for (const r of rows) this.offsets.push(this.offsets.at(-1) + r.h);
    this.spacer.style.height = `${this.offsets.at(-1)}px`;
    this.render();
  }

  render() {
    const top = this.list.scrollTop, vh = this.list.clientHeight || 600;
    let lo = 0, hi = this.rows.length;
    while (lo < hi) { // first row with bottom > top
      const mid = (lo + hi) >> 1;
      if (this.offsets[mid + 1] <= top) lo = mid + 1; else hi = mid;
    }
    const start = Math.max(0, lo - OVERSCAN);
    let end = start;
    while (end < this.rows.length && this.offsets[end] < top + vh + OVERSCAN * 60) end++;
    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      const row = this.rows[i];
      const selected = this.cb.isSelected(row);
      const el = document.createElement("div");
      el.className = `row ${row.type}${this.cb.isActive(row) ? " active" : ""}`;
      el.style.top = `${this.offsets[i]}px`;
      el.style.height = `${row.h}px`;
      el.dataset.idx = i;
      el.id = rowDomId(i);
      el.setAttribute("role", "option");
      el.setAttribute("tabindex", "-1");
      el.setAttribute("aria-selected", selected ? "true" : "false");
      el.setAttribute("aria-checked", selected ? "true" : "false");
      el.innerHTML = rowHtml(row, selected);
      frag.appendChild(el);
    }
    this.rowsEl.replaceChildren(frag);
    // Keep focus on #list (existing global key handler) while exposing the
    // active row to AT via aria-activedescendant.
    const activeIdx = this.rows.findIndex((r) => this.cb.isActive(r));
    if (activeIdx >= start && activeIdx < end) {
      this.list.setAttribute("aria-activedescendant", rowDomId(activeIdx));
    } else {
      this.list.removeAttribute("aria-activedescendant");
    }
    if (this.rows.length === 0 && !document.getElementById("list-error")) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.id = "list-empty";
      const p = document.createElement("p");
      p.textContent = "no matches · clear filters";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "clear filters";
      btn.addEventListener("click", () => this.cb.onClearFilters?.());
      empty.append(p, btn);
      this.rowsEl.appendChild(empty);
    }
    // #list-meta-text is owned by main.js paintSelAll(); don't clobber the checkbox.
    if (!document.getElementById("list-meta-text")) {
      const meta = document.getElementById("list-meta");
      if (meta) meta.textContent = `${this.rows.length} rows`;
    }
  }

  reveal(idx) {
    if (idx < 0 || idx >= this.rows.length) return;
    const top = this.offsets[idx], bottom = this.offsets[idx + 1];
    if (top < this.list.scrollTop) this.list.scrollTop = top;
    else if (bottom > this.list.scrollTop + this.list.clientHeight) {
      this.list.scrollTop = bottom - this.list.clientHeight;
    }
    this.render();
  }
}

export function renderDetail(target) {
  const el = document.getElementById("detail");
  if (!target) {
    el.innerHTML = `<h3>Details</h3><p class="hint">Select a mapset or difficulty.</p>`;
    return;
  }
  if (target.type === "map") {
    const m = target.map;
    const link = m.beatmap_id > 0
      ? `<a href="https://osu.ppy.sh/beatmaps/${m.beatmap_id}" target="_blank" rel="noopener">open on website</a>` : "";
    const art = m.id
      ? `<img class="art" src="/api/art?id=${encodeURIComponent(m.id)}" alt="" onerror="this.remove()">`
      : "";
    el.innerHTML = art + `<h3>${escapeHtml(m.title)}</h3>` +
      `<p class="hint">${escapeHtml(m.artist)} · mapped by ${escapeHtml(m.creator)}</p>` +
      `<table>` +
      `<tr><td class="k">difficulty</td><td>${escapeHtml(m.diff)}</td></tr>` +
      `<tr><td class="k">mode</td><td>${escapeHtml(m.mode_name || "")}</td></tr>` +
      `<tr><td class="k">stars</td><td>${(m.stars || 0).toFixed(2)}</td></tr>` +
      `<tr><td class="k">bpm / length</td><td>${escapeHtml(m.bpm || "—")} · ${Math.round((Number(m.length_ms) || 0) / 1000)}s</td></tr>` +
      `<tr><td class="k">AR CS OD HP</td><td>${escapeHtml(m.ar)} ${escapeHtml(m.cs)} ${escapeHtml(m.od)} ${escapeHtml(m.hp)}</td></tr>` +
      `<tr><td class="k">status</td><td>${escapeHtml(m.ranked || "")}</td></tr>` +
      `<tr><td class="k">played</td><td>${isPlayed(m) ? "yes" : "no"} (local: ${m.played_local ? "yes" : "no"}, online: ${m.played_online ? "yes" : "no"})</td></tr>` +
      `</table><p>${link}</p>`;
    return;
  }
  const s = target.set;
  const rows = s.maps.map((m) =>
    `<tr><td>${escapeHtml(m.diff)}</td><td>★${(m.stars || 0).toFixed(2)}</td>` +
    `<td>${isPlayed(m) ? "yes" : "no"}</td></tr>`).join("");
  const firstId = s.maps[0]?.id;
  const art = firstId
    ? `<img class="art" src="/api/art?id=${encodeURIComponent(firstId)}" alt="" onerror="this.remove()">`
    : "";
  el.innerHTML = art + `<h3>${escapeHtml(s.title)}</h3>` +
    `<p class="hint">${escapeHtml(s.artist)} · ${s.played}/${s.maps.length} played</p>` +
    `<table>${rows}</table>`;
}
