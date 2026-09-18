/* App wiring: state, filters, list, keyboard, scan/mode/online jobs. */
import { cachedVersion, detectInstalls, library, pollJob, setMode, startOnlineCheck, startScan, status, useInstall } from "./api.js";
import { initAuth, renderAccountChip } from "./auth.js";
import { initBulk } from "./bulk.js";
import { buildRows, defaultFilters, loadPrefs, savePrefs, summarize } from "./store.js";
import { ListView, renderDetail, renderStats, toast } from "./views.js";

const state = {
  maps: [],
  rows: [],
  filters: defaultFilters(),
  selection: new Set(),
  expanded: new Set(),
  activeIdx: -1,
  mode: "stable",
  memoKey: "",
};

const $ = (id) => document.getElementById(id);
let listView, bulk;

/* ---------- derived state ---------- */

function visibleIds() {
  const ids = [];
  for (const r of state.rows) {
    if (r.type === "map") ids.push(r.map.id);
    else for (const m of r.set.maps) ids.push(m.id);
  }
  return ids;
}

function rowId(row) {
  return row.type === "set" ? `set:${row.set.key}` : row.map.id;
}

function isSelected(row) {
  if (row.type === "set") return row.set.maps.every((m) => state.selection.has(m.id));
  return state.selection.has(row.map.id);
}

function refresh(persist = true) {
  // cachedVersion() is the in-memory /api/library ETag: it busts on any
  // content change (including online-check flag flips) with no extra fetch.
  const key = JSON.stringify([state.filters, state.maps.length, cachedVersion()]);
  if (key !== state.memoKey) {
    state.rows = buildRows(state.maps, state.filters, state.expanded);
    state.memoKey = key;
    listView.setRows(state.rows);
  } else {
    listView.render();
  }
  bulk.update(state.selection.size);
  paintSelAll();
  const active = state.rows[state.activeIdx];
  renderDetail(active || null);
  if (persist) savePrefs(state.filters, state.selection);
}

/* ---------- selection ---------- */

function toggleRow(row) {
  if (row.type === "set") {
    const all = row.set.maps.every((m) => state.selection.has(m.id));
    for (const m of row.set.maps) {
      if (all) state.selection.delete(m.id);
      else state.selection.add(m.id);
    }
  } else if (state.selection.has(row.map.id)) {
    state.selection.delete(row.map.id);
  } else {
    state.selection.add(row.map.id);
  }
}

function onRowClick(row, ev) {
  const idx = state.rows.indexOf(row);
  if (ev.shiftKey && state.activeIdx >= 0) {
    const [a, b] = [state.activeIdx, idx].sort((x, y) => x - y);
    for (let i = a; i <= b; i++) {
      const r = state.rows[i];
      if (r.type === "map") state.selection.add(r.map.id);
      else for (const m of r.set.maps) state.selection.add(m.id);
    }
  } else {
    toggleRow(row);
    if (row.type === "set") {
      if (state.expanded.has(row.set.key)) state.expanded.delete(row.set.key);
      else state.expanded.add(row.set.key);
      state.memoKey = ""; // expansion changes rows
    }
  }
  state.activeIdx = idx;
  refresh();
}

/* ---------- data loading ---------- */

function showJob(label, jobPromise, onDone) {
  const box = $("job-progress");
  $("job-label").textContent = label;
  $("job-bar").value = 0;
  box.hidden = false;
  return jobPromise.then(
    (j) => {
      // Show a clear "done" state, then get out of the way.
      $("job-label").textContent = `${label} complete`;
      $("job-bar").value = 100;
      setTimeout(() => {
        box.hidden = true;
      }, 2500);
      onDone?.(j);
    },
    (e) => {
      box.hidden = true;
      toast(`${label} failed: ${e.message}`, "error");
    },
  );
}

async function reloadLibrary() {
  try {
    const data = await library();
    if (!data.notModified) {
      state.maps = data.maps;
      state.memoKey = "";
    }
    renderStats(state.maps, summarize(state.maps), state.mode);
    refresh(false);
  } catch (e) {
    toast(`cannot load library: ${e.message}`, "error");
  }
}

let scanning = false;

function runScan() {
  if (scanning) {
    toast("scan already running");
    return;
  }
  scanning = true;
  $("scan-btn").disabled = true;
  const p = (async () => {
    const id = await startScan(state.mode, true); // scan always reads everything
    await pollJob(id, (j) => {
      const total = j.total || 1;
      $("job-label").textContent = `scan ${Math.min(j.done, total)}/${total}`;
      $("job-bar").value = (100 * Math.min(j.done, total)) / total;
    });
  })();
  showJob("scan", p, () => reloadLibrary()).finally(() => {
    scanning = false;
    $("scan-btn").disabled = false;
  });
}

/* ---------- boot ---------- */

function bindFilters() {
  const f = state.filters;
  let deb = 0;
  $("q").value = f.q;
  $("q").addEventListener("input", (ev) => {
    clearTimeout(deb);
    deb = setTimeout(() => {
      f.q = ev.target.value;
      state.activeIdx = -1;
      refresh();
    }, 200);
  });
  const bind = (id, key, parse = (v) => v) => {
    $(id).value = f[key];
    $(id).addEventListener("change", (ev) => {
      f[key] = parse(ev.target.value);
      state.activeIdx = -1;
      refresh();
    });
  };
  bind("played", "played");
  bind("ranked", "ranked");
  bind("sort", "sort");
  bindStars();
  bindSelAll();
  document.querySelectorAll('input[name="view"]').forEach((r) => {
    r.checked = r.value === f.view;
    r.addEventListener("change", () => {
      f.view = document.querySelector('input[name="view"]:checked').value;
      state.activeIdx = -1;
      state.memoKey = "";
      refresh();
    });
  });
}

function bindStars() {
  const f = state.filters;
  const minR = $("smin-r"), maxR = $("smax-r");
  if (Number(f.smin) > 10) f.smin = 10;
  minR.value = Math.min(10, Math.max(0, Number(f.smin) || 0));
  maxR.value = Number(f.smax) >= 10 ? 10 : Math.min(10, Math.max(0, Number(f.smax) || 0));
  if (parseFloat(minR.value) > parseFloat(maxR.value)) minR.value = maxR.value;
  const paint = () => {
    const lo = parseFloat(minR.value), hi = parseFloat(maxR.value);
    $("sval").textContent = `${lo} – ${hi >= 10 ? "∞" : hi}`;
  };
  paint();
  const onInput = (ev) => {
    let lo = parseFloat(minR.value), hi = parseFloat(maxR.value);
    if (lo > hi) {
      if (ev.target === minR) { hi = lo; maxR.value = String(hi); }
      else { lo = hi; minR.value = String(lo); }
    }
    f.smin = lo;
    f.smax = hi >= 10 ? 99 : hi;
    paint();
    state.activeIdx = -1;
    refresh();
  };
  minR.addEventListener("input", onInput);
  maxR.addEventListener("input", onInput);
}

function paintSelAll() {
  const box = $("sel-all"), label = $("list-meta-text");
  if (!box || !label) return;
  const vis = visibleIds();
  const sel = vis.filter((id) => state.selection.has(id)).length;
  label.textContent = `${vis.length} of ${state.maps.length} shown`;
  box.checked = vis.length > 0 && sel === vis.length;
  box.indeterminate = sel > 0 && sel < vis.length;
}

function bindSelAll() {
  $("sel-all").addEventListener("click", () => {
    const vis = visibleIds();
    const all = vis.length > 0 && vis.every((id) => state.selection.has(id));
    if (all) for (const id of vis) state.selection.delete(id);
    else for (const id of vis) state.selection.add(id);
    refresh();
  });
}

function bindKeyboard() {
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "/" && document.activeElement !== $("q")) {
      ev.preventDefault();
      $("q").focus();
      return;
    }
    if (!["ArrowDown", "ArrowUp", " "].includes(ev.key)) return;
    if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || "")) return;
    ev.preventDefault();
    if (ev.key === " ") {
      const row = state.rows[state.activeIdx];
      if (row) {
        toggleRow(row);
        refresh();
      }
      return;
    }
    const d = ev.key === "ArrowDown" ? 1 : -1;
    state.activeIdx = Math.min(state.rows.length - 1, Math.max(0, state.activeIdx + d));
    listView.reveal(state.activeIdx);
    refresh(false);
  });
}

function bindTopbar() {
  const paintRuleset = () => {
    document.querySelectorAll(".ruleset button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.ruleset === state.filters.mode));
    });
  };
  document.querySelectorAll(".ruleset button").forEach((b) => {
    b.addEventListener("click", () => {
      state.filters.mode = b.dataset.ruleset;
      state.activeIdx = -1;
      paintRuleset();
      refresh();
    });
  });
  paintRuleset();
  state.paintRuleset = paintRuleset;

  const paintMode = () => {
    $("mode-stable").classList.toggle("active", state.mode === "stable");
    $("mode-lazer").classList.toggle("active", state.mode === "lazer");
  };
  $("mode-stable").addEventListener("click", () => switchMode("stable"));
  $("mode-lazer").addEventListener("click", () => switchMode("lazer"));
  paintMode();
  state.paintMode = paintMode;

  $("scan-btn").addEventListener("click", () => runScan());
  $("online-btn").addEventListener("click", async () => {
    try {
      const id = await startOnlineCheck();
      showJob("online check", pollJob(id, (j) => {
        $("job-label").textContent = `online ${j.done}/${j.total || "…"}`;
        if (j.total) $("job-bar").value = (100 * j.done) / j.total;
      }), () => reloadLibrary());
    } catch (e) {
      toast(`online check: ${e.message}`, "error");
    }
  });
}

async function switchMode(mode) {
  if (mode === state.mode) return;
  try {
    await setMode(mode);
  } catch (e) {
    toast(`mode switch failed: ${e.message}`, "error");
    return;
  }
  state.mode = mode;
  state.selection.clear();
  state.expanded.clear();
  state.activeIdx = -1;
  state.memoKey = "";
  state.paintMode();
  await boot();
}

async function boot() {
  const st = await status().catch((e) => {
    toast(`server unreachable: ${e.message}`, "error");
    return null;
  });
  if (!st) return;
  state.mode = st.mode;
  state.paintMode?.();
  renderAccountChip(st.auth);
  if (st.scan.cached || st.counts.diffs) {
    $("setup").hidden = true;
    await reloadLibrary();
  } else {
    renderStats([], { diffs: 0, sets: 0, played: 0, unplayed: 0 }, state.mode);
    listView.setRows([]);
    showSetup(st.installs || []);
  }
}

function showSetup(installs) {
  const box = $("setup"), btns = $("setup-btns"), text = $("setup-text");
  btns.replaceChildren();
  if (!installs.length) {
    text.textContent = "No osu! install found in the usual places.";
  } else {
    text.textContent = "Found on this machine:";
  }
  for (const inst of installs.slice(0, 4)) {
    const b = document.createElement("button");
    const markers = inst.kind === "stable"
      ? [inst.songs && "Songs", inst.osu_db && "osu!.db"].filter(Boolean).join("+")
      : [inst.files && "files", inst.realm && "client.realm"].filter(Boolean).join("+");
    b.textContent = `use ${inst.kind} (${markers})`;
    b.title = inst.path;
    b.addEventListener("click", () => adoptInstall(inst.kind, inst.path));
    const wrap = document.createElement("span");
    wrap.appendChild(b);
    const path = document.createElement("span");
    path.className = "path";
    path.textContent = ` ${inst.path}`;
    wrap.appendChild(path);
    btns.appendChild(wrap);
  }
  // manual fallback: install lives somewhere unusual
  const input = document.createElement("input");
  input.id = "setup-path";
  input.size = 40;
  input.placeholder = "…or paste your osu! folder path here";
  input.setAttribute("aria-label", "osu install folder path");
  const useStable = document.createElement("button");
  useStable.textContent = "use as stable";
  useStable.addEventListener("click", () => adoptInstall("stable", input.value.trim()));
  const useLazer = document.createElement("button");
  useLazer.textContent = "use as lazer";
  useLazer.addEventListener("click", () => adoptInstall("lazer", input.value.trim()));
  const wrap = document.createElement("span");
  wrap.append(input, useStable, useLazer);
  btns.appendChild(wrap);
  box.hidden = false;
}

async function adoptInstall(kind, path) {
  if (!path) {
    toast("paste your osu! folder path first", "error");
    return;
  }
  try {
    await useInstall(kind, path);
    state.mode = kind;
    state.paintMode();
    $("setup").hidden = true;
    toast(`using ${kind} at ${path}`);
    runScan();
  } catch (e) {
    toast(`cannot use install: ${e.message}`, "error");
  }
}

function main() {
  const prefs = loadPrefs();
  state.filters = { ...defaultFilters(), ...prefs.filters };
  state.selection = new Set(prefs.selection);

  listView = new ListView({
    isSelected,
    isActive: (row) => state.rows[state.activeIdx] === row,
    onRowClick,
  });
  bulk = initBulk({
    selectedIds: () => [...state.selection],
    selectFiltered: () => {
      for (const id of visibleIds()) state.selection.add(id);
      refresh();
    },
    invertSelection: () => {
      const vis = new Set(visibleIds());
      const next = new Set([...state.selection].filter((id) => !vis.has(id)));
      for (const id of vis) if (!state.selection.has(id)) next.add(id);
      state.selection = next;
      refresh();
    },
    clearSelection: () => {
      state.selection.clear();
      refresh();
    },
  });
  initAuth(() => boot());

  bindFilters();
  bindKeyboard();
  bindTopbar();
  renderDetail(null);
  boot();
}

main();

// test hook: pure selection helpers without DOM
export const __test = { visibleIds, toggleRow, state };
