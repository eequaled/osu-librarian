# WebUI Build Spec — osu! Librarian

## 0. Goal

Replace `report.py` (static dump) and `app_tk.py` (desktop) with a **local web app**
that is genuinely pleasant to use on libraries of 10k–100k difficulties:

- mode switch **stable | lazer**, rescan with progress
- fast filter: text search, played/unplayed, mode, ★ range, ranked status, sort
- view toggle **by mapset | by difficulty** (mirrors osu! song-select grouping)
- multiselect (checkboxes, keyboard, select-filtered, invert) + bulk export
- account link (osu! API v2 OAuth) + online played check with progress
- **caching at every layer** so repeat visits are instant and rescans are incremental

## 1. Stack (locked)

- **Backend:** Python, **stdlib only** (`http.server.ThreadingHTTPServer` + `json` +
  `urllib`), reusing existing `src/` (`config`, `stable_scanner`, `lazer_scanner`,
  `library`, `osu_api`). No Flask/FastAPI/npm — zero-install stays a feature.
- **Frontend:** no-build static app in `web/` — `index.html`, `styles.css`,
  ES modules (`api.js`, `store.js`, `views.js`, `main.js`). No bundler, no deps.
- **Tests:** `unittest` (stdlib), runnable via `python3 -m unittest discover -s tests`.
- **Run:** `python3 main.py web [--port 8787]` serves API + static from one process.

## 2. Architecture

```
browser ──HTTP──> main.py web
                    ├─ /            web/index.html (+ styles.css, *.js)
                    ├─ /api/*       JSON API (server.py)
                    │                 ├─ library state  <- src/library.py
                    │                 ├─ scanners       <- src/*_scanner.py
                    │                 ├─ scan cache     <- .cache/scan-<mode>.json + manifest
                    │                 ├─ jobs           <- background scan / online-check
                    │                 └─ osu! API       <- src/osu_api.py + api cache (TTL)
                    └─ /api/export  file download
```

### 2.1 API contract (frozen before parallel work — all agents code against this)

| Method | Path | Req body / query | Resp (JSON unless noted) |
|---|---|---|---|
| GET | `/api/status` | — | `{mode, songs_ok, db_ok, lazer_ok, counts:{diffs,sets,played,unplayed}, scan:{version, finished_at, incremental}, jobs:{active}}` |
| GET | `/api/library` | `?view=set\|diff` optional (server returns flat diffs always; grouping is client-side) | `{version, maps:[Beatmap…]}` + `ETag: "<version>"`; `If-None-Match` → `304` |
| POST | `/api/scan` | `{mode?, fresh?}` | `{job_id}` (`fresh=1` ignores scan cache) |
| GET | `/api/jobs/:id` | — | `{id, kind:scan\|online, state:queued\|running\|done\|error, done, total, error?}` |
| GET | `/api/auth/url` | — | `{url}` (from settings `client_id`/`redirect_uri`) |
| POST | `/api/auth/code` | `{code}` | `{ok:true}` (token persisted server-side, never sent to client) |
| POST | `/api/online-check` | `{}` (uses linked user) | `{job_id}` (bulk check, cached, rate-limited) |
| POST | `/api/export` | `{ids:[…], format:json\|txt\|collection}` | file download (`selection.json`, `selection.txt`, `collection.db`) |

`Beatmap` JSON = existing `library.to_dict_list()` shape (flat diff objects with
`id, set_id, artist, title, creator, diff, mode_name, stars, bpm, ranked, grade, played_local, played_online, beatmap_id, …`).
`played` (derived) = `played_local || played_online`. No new required fields;
agents may add optional ones but must not rename existing ones.

### 2.2 Caching design (all four layers ship)

1. **Scan cache (server, biggest win).** `.cache/scan-<mode>.json` + `.cache/manifest.json`
   storing per-folder mtimes + file sizes + `osu!.db`/`scores.db` mtimes.
   Rescan re-parses only changed folders/files and reuses the rest.
   `POST /api/scan` without `fresh` = incremental; `version` = hash of
   (manifest + counts) and becomes the `/api/library` ETag.
2. **Online-score cache (server).** Extend `.api_cache.json` values to
   `{played: bool, at: epoch}` with **7-day TTL**; background job walks only
   `beatmap_id > 0` diffs, skips fresh cache hits, sleeps ~0.4s between calls,
   persists every 20 results (crash-safe), reports `done/total` via jobs endpoint.
3. **HTTP caching.** `ETag`/`If-None-Match` on `/api/library` (→ `304` when the
   client already has `version`); `Cache-Control: no-store` on jobs/auth;
   `Last-Modified` on static assets.
4. **Client caching.** `store.js` keeps last `version` + maps in memory and
   `localStorage` for settings/filters/selection (never the full library);
   search input debounced ~200ms; filter results memoized per (filters+version);
   list rendered **windowed** (only visible rows in DOM, overscan ~10) so 100k
   rows stay smooth.

## 3. UX spec (what "good" means here)

- **Layout:** left sidebar (mode switch, library stats, scan button + progress,
  account link state), top filter bar (search, played, mode, ★min–max, ranked,
  sort, view toggle), main list, right detail pane for focused mapset/diff,
  bottom bulk-action bar (appears when selection > 0).
- **List:** grouped headers in mapset view (artist — title, x/y played, max ★,
  expand/collapse), flat rows in difficulty view; row = checkbox, played dot,
  title [diff], mapper, ★, mode chip, grade, status. Multi-select via
  shift-click range, ctrl/cmd-click, space toggles focused row, `/` focuses search.
- **States:** skeleton rows while loading, clear empty states
  ("no unplayed osu! maps ★4–5 — widen filters"), inline error toasts with retry.
- **Detail pane:** cover placeholder, metadata grid, per-diff table with played
  source (local/online), "open on osu.ppy.sh" link per diff.
- **Dark theme** default (osu!-adjacent), respects `prefers-color-scheme`;
  WCAG-AA contrast, all actions keyboard-reachable, visible focus rings.

## 4. Workstreams (2–3 subagents)

Contracts in §2.1/§2.2 are frozen; agents work in parallel and integrate at the end.

### Agent 1 — Backend API + caching + jobs
Files: `src/server.py` (new), `src/cache.py` (new), `src/jobs.py` (new),
extend `src/osu_api.py` (TTL cache), `main.py` (`web` subcommand), tests.
- `GET /api/status|library`, `POST /api/scan`, `GET /api/jobs/:id`,
  auth + online-check endpoints, `POST /api/export` (json/txt/collection.db),
  static-file serving for `web/`, ETag/`If-None-Match` handling.
- Scan-cache manifest + incremental rescan for **both** modes; thread-safe job
  registry; token stored server-side only (`0600` file).
- Tests: API smoke tests against `tests/` mock install (status→scan→job→library
  round-trip), cache-hit test (second scan parses ~0 files), ETag 304 test,
  TTL-expiry unit test. `python3 -m unittest discover -s tests` green.
- Done when: cold scan → cached reload is instant; `fresh=1` bypass works;
  100k-row library serializes in < 2s.

### Agent 2 — Frontend core (browse/filter/select)
Files: `web/index.html`, `web/styles.css`, `web/api.js`, `web/store.js`,
`web/views.js`, `web/main.js`.
- Implements §3 layout minus account/export surfaces (placeholders with hooks).
- `api.js`: typed fetch wrappers honoring ETag (`If-None-Match`, in-memory
  version check). `store.js`: filters (mirror `Filters`), memoized
  `getVisible()`, mapset grouping, selection set + shift-range + persistence.
  `views.js`: windowed list (overscan, absolute-positioned rows), filter bar,
  detail pane, toasts, empty/loading states.
- Quality: no framework, no inline handlers, no `innerHTML` with unescaped data
  (use `textContent`/escape helper), keyboard map (`/`, space, arrows, shift).
- Done when: with mock `library.json` served, all filters + view toggle +
  selection ops work at 60fps scroll on 20k rows; `main.js` < 600 lines total
  across modules (split further if larger).

### Agent 3 — Account + online check + export + QA
Files: `web/auth.js`, `web/bulk.js`, backend export/auth completion (pair with
Agent 1's stubs), `tests/test_web_e2e.py`, this spec's acceptance checklist.
- Auth UI (link/unlink, user chip), online-check progress bar wired to jobs API,
  export dialog (json/txt/collection.db download), "open on website" links.
- E2E smoke: serve app + mock library, assert status/library/jobs/export
  endpoints + ETag behavior; perf smoke (time-to-interactive, scroll fps notes);
  a11y pass (focus order, labels, contrast); update `README.md` (run + OAuth
  setup + caching explanation).
- Done when: full loop works — scan → filter unplayed → select → export, and
  link account → online-check marks `played_online` with progress.

**With 2 subagents:** merge Agents 2+3 (one owns all of `web/`, other owns
backend). Agent 1's API contract + stub server comes first (half-day), then both
run parallel.

## 5. File layout (final)

```
main.py                  + `web` subcommand
src/server.py            HTTP API + static serving + ETag
src/cache.py             scan manifest + incremental logic
src/jobs.py              background job registry
src/osu_api.py           (+TTL cache, persist-every-20)
src/export.py            (new, Agent 1/3: json/txt/collection.db writers)
web/index.html web/styles.css web/api.js web/store.js web/views.js
    web/auth.js web/bulk.js web/main.js
.cache/                  (gitignored) scan-<mode>.json + manifest.json
tests/test_server.py tests/test_cache.py tests/test_web_e2e.py
docs/webui-spec.md       (this file)
```

## 6. Quality bar (applies to all agents)

- stdlib-only backend, zero-dep frontend; no new runtime dependencies.
- Typed Python signatures, docstrings on public functions, no dead code.
- Every endpoint + cache path covered by a test; frontend logic (filter/group/
  select) unit-testable — keep it DOM-free in `store.js`-level pure functions
  with a `tests/test_store.mjs`-style node check **or** mirrored Python tests
  against `src/library.py` semantics (pick one, document it).
- `README.md` updated; `settings.example.json` gains `web:{port}` if needed.
- No secrets in repo, tokens `0600`, `.gitignore` covers `.cache/`, exports.

## 7. Acceptance checklist

- [ ] `python3 main.py web` → app loads, shows mock/mock-real library
- [ ] rescan stable + lazer with progress; second load instant (scan cache)
- [ ] filters (text/played/mode/★/status/sort) + mapset/difficulty toggle correct
- [ ] selection (click/shift/space/select-filtered/invert) + count + export all formats
- [ ] OAuth link → online-check with progress → `played_online` updates
- [ ] `304` on unchanged library; TTL respected; all tests green
