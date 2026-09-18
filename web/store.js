/* Selection + filter state. DOM-free pure logic (mirrors src/library.py).
   views.js renders; main.js wires events. */

export function defaultFilters() {
  return { q: "", played: "all", mode: "all", smin: 0, smax: 99,
           ranked: "all", sort: "title", view: "difficulty" };
}

export function isPlayed(m) {
  return !!(m.played_local || m.played_online);
}

/* Star slider: dragging fires input per pixel, so main.js repaints the label
 * immediately but debounces the full filter+sort+persist rebuild by this long. */
export const STAR_REBUILD_DEBOUNCE_MS = 175;

/* Clamp a crossed dual-range pair: the handle being dragged wins. Pure so the
 * slider math stays testable without DOM. changed is "min", "max", or "". */
export function resolveStarRange(lo, hi, changed) {
  lo = Number(lo);
  hi = Number(hi);
  if (lo > hi) {
    if (changed === "min") hi = lo;
    else lo = hi;
  }
  return [lo, hi];
}

/* Keyboard scope for the list shortcuts (arrows/space): they must only fire
 * when #list has focus (or nothing focusable does), never when a button,
 * dialog, or input is focused. Pure so it stays testable without DOM. */
export function listShortcutAllowed(activeTag, focusInList, focusIsBody) {
  if (focusInList || focusIsBody) return true;
  if (!activeTag) return true; // nothing focused
  return false;
}

/* Single-pass visible/selected counter for the select-all checkbox: walks rows
 * once instead of building an id list and filtering it afterwards. */
export function countVisibleSelection(rows, selection) {
  let total = 0, sel = 0;
  for (const r of rows) {
    if (r.type === "map") {
      total++;
      if (selection.has(r.map.id)) sel++;
    } else {
      for (const m of r.set.maps) {
        total++;
        if (selection.has(m.id)) sel++;
      }
    }
  }
  return { total, sel };
}

export function searchBlob(m) {
  return [m.artist, m.title, m.creator, m.diff, m.source, m.tags,
          String(m.beatmap_id ?? "")].join(" ").toLowerCase();
}

const SORTERS = {
  title: (a, b) => (a.title || "").localeCompare(b.title || "") || diffCmp(a, b),
  artist: (a, b) => (a.artist || "").localeCompare(b.artist || "") || diffCmp(a, b),
  creator: (a, b) => (a.creator || "").localeCompare(b.creator || "") || diffCmp(a, b),
  bpm: (a, b) => (a.bpm || 0) - (b.bpm || 0),
  length: (a, b) => (a.length_ms || 0) - (b.length_ms || 0),
  stars: (a, b) => (a.stars || 0) - (b.stars || 0),
  rank: (a, b) => (isPlayed(a) - isPlayed(b)) || String(a.grade || "zz").localeCompare(String(b.grade || "zz")),
};

function diffCmp(a, b) {
  return (a.diff || "").localeCompare(b.diff || "");
}

export function visibleMaps(maps, f) {
  const q = (f.q || "").trim().toLowerCase();
  const out = maps.filter((m) => {
    if (f.played === "played" && !isPlayed(m)) return false;
    if (f.played === "unplayed" && isPlayed(m)) return false;
    if (f.mode !== "all" && m.mode_name !== f.mode) return false;
    const st = m.stars || 0;
    if (!(st >= f.smin && st <= f.smax) && !(st === 0 && f.smin <= 0)) return false;
    if (f.ranked !== "all" && m.ranked !== f.ranked) return false;
    if (q && !searchBlob(m).includes(q)) return false;
    return true;
  });
  return out.sort(SORTERS[f.sort] || SORTERS.title);
}

export function groupIntoSets(maps) {
  const order = new Map();
  for (const m of maps) {
    if (!order.has(m.set_id)) {
      order.set(m.set_id, { key: m.set_id, artist: m.artist, title: m.title,
                            creator: m.creator, maps: [] });
    }
    order.get(m.set_id).maps.push(m);
  }
  const sets = [...order.values()];
  for (const s of sets) {
    s.maps.sort((a, b) => (a.stars || 0) - (b.stars || 0));
    s.played = s.maps.filter(isPlayed).length;
    s.maxStars = Math.max(0, ...s.maps.map((m) => m.stars || 0));
  }
  return sets.sort((a, b) => (a.artist || "").localeCompare(b.artist || "")
    || (a.title || "").localeCompare(b.title || ""));
}

/**
 * Flatten visible maps into virtual-list rows.
 * Mapset view: header row per set + child rows when expanded.
 * Returns [{type:'set'|'map', h: pxHeight, ...}].
 */
export function buildRows(maps, f, expanded) {
  if (f.view !== "mapset") {
    return visibleMaps(maps, f).map((m) => ({ type: "map", h: 56, map: m }));
  }
  const rows = [];
  for (const s of groupIntoSets(visibleMaps(maps, { ...f, sort: "title" }))) {
    rows.push({ type: "set", h: 44, set: s });
    if (expanded.has(s.key)) {
      for (const m of s.maps) rows.push({ type: "map", h: 56, map: m, child: true });
    }
  }
  return rows;
}

export function summarize(maps) {
  const local = maps.filter((m) => m.played_local).length;
  const onlineOnly = maps.filter((m) => !m.played_local && m.played_online).length;
  return { diffs: maps.length, sets: new Set(maps.map((m) => m.set_id)).size,
           played: local + onlineOnly, unplayed: maps.length - local - onlineOnly,
           local, onlineOnly };
}

/* persistent UI prefs (filters + selection), never the library itself */
const LS_KEY = "osu-librarian-v1";

export function loadPrefs() {
  try {
    return { filters: defaultFilters(), selection: [], ...(JSON.parse(localStorage.getItem(LS_KEY) || "{}")) };
  } catch {
    return { filters: defaultFilters(), selection: [] };
  }
}

export function savePrefs(filters, selection) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ filters, selection: [...selection].slice(0, 20000) }));
  } catch { /* quota: selection simply won't persist */ }
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
