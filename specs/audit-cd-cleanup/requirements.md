# Requirements — Audit C/D cleanup

## Introduction
The 151-finding audit has been executed through the S, A and B tiers. This spec
covers the remaining actionable C-tier (polish) and D-tier (trivial) findings —
roughly 20 concrete items that survived spot-checking after the earlier fix
commits. Everything here is small, low-risk and must not change user-visible
behavior except where a requirement says so.

Constraints (repo-wide, unchanged): stdlib-only Python backend, vanilla-JS
no-build frontend, no secrets in repo or logs. All three suites must stay green
after every task: `python3 -m unittest discover -s tests`, `python3 -m unittest
discover -s relay`, `sh scripts/check-web.sh`.

## Requirements

### Requirement 1 — Relay polish
**User Story:** As a relay operator, I want consistent HTTP semantics and
merged helper code, so the relay stays maintainable and honest about its
limits.

#### Acceptance Criteria
1. WHEN a rate-limited response is returned by any relay endpoint THEN the
   response SHALL include a correct `Retry-After` header (audit C: some
   paths returned the JSON field but no header).
2. IF `_post` and `_get` in `relay/server.py` duplicate request logic THEN they
   SHALL be merged into one helper without behavior change.
3. WHEN the README documents the loopback port range for state tickets THEN it
   SHALL match what `web/auth.js` actually accepts.
4. IF a request body exceeds the limit THEN the relay SHALL drain the body
   before replying 413 so keep-alive connections stay in sync (verify; add a
   regression test only if missing).

### Requirement 2 — Auth/frontend polish
**User Story:** As a user linking my osu! account, I want clear feedback for
every failure mode, so I never wonder whether linking worked.

#### Acceptance Criteria
1. WHEN `/api/auth/pair` returns 429 THEN the auth dialog SHALL show a
   rate-limited message distinct from "unreachable".
2. WHEN the user presses Esc while OAuth polling is active THEN the dialog
   SHALL show a hint that closing stops the link check (behavior unchanged;
   messaging only).
3. WHEN the account is linked THEN the status chip SHALL show the osu!
   username alongside the user id (already partially done — verify and keep).
4. IF `submitCode()` in `web/api.js` has no UI surface THEN it SHALL be removed
   from `api.js` together with its doc mention, OR wired to a paste-code
   fallback; the chosen option is "remove" (docs updated accordingly).
5. WHEN `setInterval` polling overlaps a slow status fetch THEN a new tick
   SHALL be skipped while the previous fetch is still in flight.

### Requirement 3 — Backend polish
**User Story:** As a user scanning my library, I want repeated scans to be
correct and cheap, so results never depend on how many times I scanned.

#### Acceptance Criteria
1. IF the scan fingerprint is computed twice per scan cycle THEN the second
   computation SHALL be eliminated or shared (audit C: double fingerprint in
   `src/server.py`).
2. WHEN a blob file is parsed in `src/lazer_scanner.py` THEN the shared
   `_parse_blob` helper SHALL be the only parser used (no duplicated inline
   parsing remains).
3. WHEN `src/cache.py` contains unreachable or duplicated `save_scan`/
   `_lazer_keys` code THEN it SHALL be removed without changing the cache
   format.

### Requirement 4 — Docs truth
**User Story:** As a future contributor (or future session), I want docs that
match the code, so I stop triaging stale information.

#### Acceptance Criteria
1. WHEN `README.md` states test counts or layout THEN it SHALL match reality
   (227 backend tests, 59 relay tests, current tree).
2. IF `docs/STATE.md` contradicts the current state (test counts, relay host
   naming, deprecations) THEN those lines SHALL be corrected and the audit
   tiers marked done.
3. WHEN `docs/webui-spec.md` contains superseded sections THEN each SHALL
   carry a one-line SUPERSEDED marker pointing at the real behavior (the
   HISTORICAL banner exists; per-section markers do not).

## Out of scope
- Anything already fixed by the S/A/B commits (do not re-touch).
- F-tier items (verified non-bugs).
- New features (see specs/product-backlog).
