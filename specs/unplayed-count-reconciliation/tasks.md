# Implementation Plan — Unplayed count reconciliation

- [x] 1. Backend classification endpoint
  - [ ] 1.1 Add `GET /api/reconciliation` computing the three buckets + `lazer_style_unplayed` + `checking` progress from the jobs registry
  - [ ] 1.2 Tests: seeded rows (local/online-only/unplayed/corrupt), linked+unlinked, never-500
  - _Requirements: 1.1, 1.2, 1.3, 4.1, 4.3_
- [x] 2. Tri-state played filter
  - [x] 2.1 Extend `Filters.played` to `all|played|online_only|unplayed` through `library.apply_filters` + JS mirror in store.js (parity contract comment updated)
  - [x] 2.2 Filter stays client-side by design (`/api/library` ships all rows; no query param) — `<select>` gained the online_only option; tests in tests/test_reconciliation.py
  - _Requirements: 3.1, 3.2_
- [x] 3. Frontend panel
  - [ ] 3.1 `web/api.js` fetchReconciliation + `web/reconcile.js` panel (numbers, formula line, progress, CTAs for link/run-check)
  - [ ] 3.2 Bucket click → filter + highlight; prefs fallback for old saved state
  - [ ] 3.3 Sidebar entry point; bump index.html `?v=`
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2_
- [x] 4. Final gate — all three suites + node --check green (232 backend tests)
  - _Requirements: 4.1, 4.2, 4.3_
