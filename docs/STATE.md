# osu! Librarian — project state (for future sessions)

Last updated: 2026-09-18. Branch: main. Stack: Python 3.14 stdlib-only backend,
vanilla-JS no-build frontend. 227 backend test methods (51 files) + 59 relay
test methods (12 files).

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
  covered via tests/test_store_node.py (needs node); `sh scripts/check-web.sh`.
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
- One-click relay flow is DEPLOYED: https://osu-librarian-relay.onrender.com
  (Render free, Docker, frankfurt, auto-deploy from GitHub main). Service id
  srv-dam6vcf40ujc73a83kug. Shipped settings carry relay_url + relay_client_id
  (68736). osu! app callback list must include
  <relay-url>/auth/callback (localhost entry kept for BYO dev flow).
- Relay Docker image validated locally (builds, /health + /pair OK). flyctl
  installed at ~/.fly/bin (not on PATH). runs-on.dev checked: subdomain
  registry only, NOT hosting — relay stays on Render (canonical host,
  render.yaml); relay/fly.toml is kept as a fallback deploy target.
  Claimed name usable as vanity CNAME later if wanted.
- Known open product question from user history: librarian unplayed (3535) vs
  lazer "by rank" unplayed (3025) — believed to be 510 online-only scores;
  online check after linking should confirm. Sidebar now splits local/online.

## Audit status (2026-09-18)
- The 151-finding audit is fully executed through S/A/B/C/D tiers. F held 6
  verified non-bugs. All three specs in `specs/` (audit-cd-cleanup,
  unplayed-count-reconciliation, product-backlog) are executed and checked off.
- New since the audit: GET /api/reconciliation + online_only filter +
  reconcile panel; GET /api/duplicates + duplicates dialog; ? shortcut
  overlay; profile chip with avatar/unlink/switcher (POST /api/auth/unlink,
  GET /api/accounts, POST /api/accounts/switch, DELETE /api/accounts/:name —
  stored accounts in .accounts/, 0600); health badge in the topbar.
- The reconciliation panel is the tool for the 3535 vs 3025 unplayed question:
  link, run an online check, compare lazer_style_unplayed with lazer's count.
- Subagent tooling: this harness has no spawn tool and the `codebuff` CLI
  (v1.0.688 via npx) requires interactive login — parallel work stays in
  orchestrator/streams mode with per-stream commits.

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
