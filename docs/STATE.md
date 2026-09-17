# osu! Librarian — project state (for future sessions)

Last updated: 2026-09-17. Branch: main. Stack: Python 3.14 stdlib-only backend,
vanilla-JS no-build frontend. 83 backend tests + 9 relay tests, all green.

## What it is
Local web app (http://localhost:8787) that manages downloaded osu! libraries:
scan stable (`Songs/` + `osu!.db` + `scores.db`) or lazer (hashed `files/` store
+ `client.realm` via node helper), filter (played/mode/stars/status/sort,
mapset-or-difficulty views, lazer-style gamemode selector + star slider),
multiselect + export (json/txt/stable collection.db), background covers served
from the store, osu! API v2 account link + bulk online played check.

## Layout
- `main.py` — CLI: scan/report/ui/web/auth/check-online
- `src/server.py` — HTTP API + static frontend (endpoints listed in its docstring)
- `src/` — config, detect (install auto-detect), scanners (stable/lazer),
  stable_db (binary readers), osu_parser, library (model/filters),
  cache (incremental scan cache), jobs (background registry), osu_api (OAuth +
  TTL score cache), realm_export (node-helper runner), difficulty (rosu-pp-py
  fallback), export, report (static fallback), app_tk (tkinter, candidate for removal)
- `tools/realm_export/` — pinned `realm@20.2.0` helper dumping client.realm to
  JSON (node_modules present, gitignored). osu-lazer-db-reader does NOT work
  (schema too old) — do not go back to it.
- `web/` — index.html/styles.css + api/store/views/auth/bulk/main.js
- `relay/` — hosted OAuth relay (stdlib, Dockerfile + fly.toml). See below.
- `tests/` — full suite: `python3 -m unittest discover -s tests`; node logic
  covered via tests/test_store_node.py (needs node); `node --check web/*.js`.
- `docs/webui-spec.md` — STALE in places (predates mode/detect/relay). Don't
  trust blindly; server.py docstring is the real API contract.

## Runtime (this machine)
- Live server cwd: `~/osu-librarian/` (settings.json, .cache/, .token.json,
  server.log). Repo itself holds no secrets; settings.json is gitignored.
- User's real library: Flatpak lazer at
  `/home/eequaled/.var/app/sh.ppy.osu/data/osu` (~815 sets / ~4.4k diffs).
- Start: `nohup python3 <repo>/main.py web --port 8787` from `~/osu-librarian`.
  `fuser -k 8787/tcp` to stop (no pkill in this env). /tmp is NOT writable here.

## Auth status
- Server-side BYO flow works (user's own osu! app id 68736 saved in
  ~/osu-librarian/settings.json; secret lives ONLY there, never in repo).
- One-click relay flow is BUILT but NOT deployed: needs public HTTPS relay +
  `relay_url`/`relay_client_id` shipped in settings, + relay callback URL
  registered on the osu! app. Local code + tests are done and verified
  (fake-relay e2e); only ops remains.
- Relay Docker image validated locally (builds, /health + /pair OK). flyctl
  installed at ~/.fly/bin (not on PATH). runs-on.dev checked: subdomain
  registry only, NOT hosting — relay stays on Fly.io; claimed name usable as
  vanity CNAME later if wanted.
- Known open product question from user history: librarian unplayed (3535) vs
  lazer "by rank" unplayed (3025) — believed to be 510 online-only scores;
  online check after linking should confirm. Sidebar now splits local/online.

## Conventions for this repo
- Orchestrator mode: user asked that work be split across Task subagents and
  verified by the lead before commit. One commit per meaningful step, plain
  non-conventional messages ("Add X…", "Fix Y…").
- Watch for recurring edit-tool footgun: no-op edits that eat newlines
  (`def f():        import x` on one line). Always re-read edited regions;
  `python3 -m unittest` + `node --check` catch it.
- Static web/ files need no server restart; server.py changes do. Browsers
  cache aggressively — index.html pins `?v=N` on main.js/styles.css; bump N
  when shipping UI changes.
- Never write to client.realm (copy first); never log tokens/secrets/codes.
