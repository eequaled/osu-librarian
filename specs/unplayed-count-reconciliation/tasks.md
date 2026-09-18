# Implementation Plan — Unplayed count reconciliation

- [ ] 1. Backend classification endpoint
  - [ ] 1.1 Add `GET /api/reconciliation` computing the three buckets + `lazer_style_unplayed` + `checking` progress from the jobs registry
  - [ ] 1.2 Tests: seeded rows (local/online-only/unplayed/corrupt), linked+unlinked, never-500
  - _Requirements: 1.1, 1.2, 1.3, 4.1, 4.3_
- [ ] 2. Tri-state played filter
  - [ ] 2.1 Extend `Filters.played` to `all|played|online_only|unplayed` through `library.apply_filters` (old boolean callers keep working)
  - [ ] 2.2 Wire the `played=` query param on `GET /api/library`; tests for the four states
  - _Requirements: 3.1, 3.2_
- [ ] 3. Frontend panel
  - [ ] 3.1 `web/api.js` fetchReconciliation + `web/reconcile.js` panel (numbers, formula line, progress, CTAs for link/run-check)
  - [ ] 3.2 Bucket click → filter + highlight; prefs fallback for old saved state
  - [ ] 3.3 Sidebar entry point; bump index.html `?v=`
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2_
- [ ] 4. Final gate — all three suites + node --check green
  - _Requirements: 4.1, 4.2, 4.3_
