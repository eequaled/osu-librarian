# Audit spec — bugs, code quality, nice-to-haves

Goal: a full read-only audit of the repo producing ranked findings and a
backlog. NO code fixes during the audit; fixes are planned after the user
approves the report. Constraints hold for all streams: stdlib-only Python
backend, vanilla-JS no-build frontend, no secrets in repo or logs, plain
`node --check web/*.js` + `python3 -m unittest discover -s tests` (+ `-s relay`)
must stay green (audit adds no code, so just don't break anything).

Report contract (every finding): `[severity] file:line — symptom/repro —
suggested fix`. Severities: S1 wrong behavior/data loss, S2 degraded UX or
perf trap, S3 maintainability/drift. Nice-to-haves: separate ranked backlog
with rough size (S/M/L). Doubled findings merge under the lower stream number.

## Stream 1 — Auth/session UX (owner of web/auth.js + relay-return path)
Scope: `web/auth.js`, `web/index.html` (auth dialog), `src/server.py`
`_auth_relay_return` + `/api/auth/status`, `~/.token.json` refresh path.
Must cover the reported bug: A1 main UI still shows logged-out after
`relay-return` saves the token until the dialog is reopened (status
polling/refresh gap). Also: ticket expiry UX, relay-unreachable UX, token
refresh failure UX, double-click/double-ticket behavior, BYO flow parity.
Read-only on: `relay/server.py`. Output: findings A1.. + UX backlog.

## Stream 2 — Backend correctness (owner of src/, excluding auth endpoints)
Scope: `src/*.py` — scan pipeline, `.cache` invalidation, jobs/progress,
export (`json/txt/collection.db`), `/api/art`, detect/install paths,
realm export runner, difficulty fallback. Hunt: stale-cache wrong results,
uncaught exceptions → 500s, path/encoding edge cases (Linux + Windows),
concurrency races in jobs, silent data loss in export. Output: findings B1.. .

## Stream 3 — Frontend quality (owner of web/, excluding auth.js logic)
Scope: `web/*.js` (except auth flow logic), `web/*.css`, `web/index.html`
structure. Hunt: XSS/escaping in rendered names, perf with ~4500 diff rows
(rendering, filtering, slider), duplicated code, dead code, accessibility
basics (focus, labels, keyboard), `?v=` cache-bust drift, mobile breakage.
Output: findings C1.. + frontend backlog.

## Stream 4 — Relay security + robustness (owner of relay/)
Scope: `relay/server.py`, `relay/test_relay.py`, `render.yaml`,
`relay/Dockerfile`. Review: ticket entropy/TTL/one-time semantics,
`/token` replay/abuse, CORS scope, error-info leakage (codes/tokens must
never hit logs), secret handling (no strip bug, empty-secret fail-closed),
DoS surface (body limits, timeouts), free-tier sleep behavior UX.
Output: findings D1.. + hardening backlog. No live-env changes.

## Stream 5 — Code health + docs/test gaps (owner of repo root, docs, tests)
Scope: `main.py`, `tests/`, `docs/` (`STATE.md` accuracy, `webui-spec.md`
staleness), `README.md` if present, `settings.example.json` drift,
duplication/dead code repo-wide, naming/consistency, test gaps (uncovered
branches in Streams 1–4 findings). Output: findings E1.. + docs/test backlog.

## Stream 6 — Nice-to-haves backlog (synthesis, runs last)
Scope: whole product. Collect and rank feature/polish ideas (release
packaging, auto-update, more filters, themes, stats, i18n hints from the
JP-UI screenshot, etc.) sized S/M/L with dependencies noted. Must not
duplicate S1–S3 findings; references them instead. Output: backlog N1...

## Execution
1. Launch Streams 1–5 in parallel (disjoint file ownership; shared files
   read-only outside the owner). Stream 6 starts after 1–5 report.
2. Each stream returns only its findings list (no code edits, no commits).
3. Merge, dedupe, rank → final report to user → user approves → fixes planned.
