# Requirements — Unplayed count reconciliation

## Introduction
STATE.md records an open product question: the librarian reports **3535
unplayed** difficulties while lazer's "by rank" view shows **3025** unplayed.
The working hypothesis: ~510 maps are unplayed locally (no entry in
`scores.db`) but have online scores on osu! servers ("online-only played").
The account link + online-check pipeline (`POST /api/online-check`, osu! API
v2 scores, `played_online` flags merged into library rows) already exists and
the sidebar already splits local/online counts — but nothing walks the user
from "the numbers disagree" to "here is exactly why".

This spec makes the app itself answer the question: run or reuse an online
check, then present a reconciliation panel that accounts for every map in the
gap between the two counts.

## Requirements

### Requirement 1 — Reconciliation data
**User Story:** As a library owner, I want the app to classify every unplayed
difficulty, so the local vs online count gap is fully explained.

#### Acceptance Criteria
1. WHEN an online check completes THEN the app SHALL classify each difficulty
   row into exactly one of: `local_played`, `online_only`, `unplayed`.
2. IF the classification total equals the row count THEN the sidebar SHALL be
   able to display the three buckets; no row may be counted twice or dropped.
3. WHEN the online check has never run (or the token is unlinked) THEN the
   app SHALL show only `local_played`/`unplayed` with an affordance to link +
   run the check — never a misleading zero.

### Requirement 2 — Reconciliation panel
**User Story:** As a library owner, I want one panel that compares my
librarian unplayed count with what lazer's "by rank" would show, so I can
verify the 3535 vs 3025 discrepancy without a spreadsheet.

#### Acceptance Criteria
1. WHEN the user opens the reconciliation view THEN it SHALL show: total
   diffs, local played, online-only played, unplayed, and the derived
   "lazer-style unplayed" (unplayed + online-only = local unplayed minus
   online-only) with the formula visible.
2. WHEN the derived number disagrees with lazer's own "by rank" count beyond
   the explained gap THEN the panel SHALL say so explicitly (numbers do not
   match → list the difference) rather than silently agreeing.
3. IF the online check job is running THEN the panel SHALL reflect live
   progress (checked/total) instead of blocking.

### Requirement 3 — Drill-down
**User Story:** As a library owner, I want to click the online-only bucket
and see those maps in the main list, so I can spot-check the classification.

#### Acceptance Criteria
1. WHEN a bucket count is clicked THEN the main list SHALL apply the
   corresponding filter (played filter extended with an "online-only" state)
   and the panel SHALL highlight the active bucket.
2. IF the filter comes from the panel THEN clearing it SHALL restore the
   previous filter state exactly.

### Requirement 4 — Non-functional
1. The online check SHALL continue to respect osu! rate limits (existing
   backoff/Retry-After behavior) — no new endpoint bypasses it.
2. No secrets or tokens SHALL ever appear in the panel, logs or exports.
3. All three suites SHALL stay green; new behavior gets new tests.

## Out of scope
- Changing how `played_online` is computed (osu_api.py played-cache semantics
  were already fixed in the S-batch).
- Lazer "by rank" scores that are neither local nor on osu! servers (e.g.
  submitted-but-unranked plays) — if the gap persists after this spec, that's
  the next investigation, not this one.
