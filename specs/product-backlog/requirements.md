# Requirements — Product backlog (N-items from the audit)

## Introduction
The audit's Stream 6 produced a ranked nice-to-haves backlog. This spec
commits to the five items the owner selected: profile header (N4), duplicates
finder (N6), shortcut help overlay (N8), multi-account (N10), and relay/API
health badge (N11). Themes (N7) stay optional; i18n (N12) is explicitly
deferred (large effort). Each item ships independently; order is by value and
dependency.

## Requirements

### Requirement 1 — Profile header (N4)
**User Story:** As a linked user, I want my osu! identity visible in the app
shell, so I always know which account the played data belongs to.

#### Acceptance Criteria
1. WHEN the account is linked THEN the app shell SHALL show a header chip
   with the avatar (or a fallback monogram), username and user id.
2. IF the user is not linked THEN the chip SHALL show a "Link account"
   affordance that opens the existing auth dialog.
3. WHEN the link state changes (link, unlink, refresh) THEN the chip SHALL
   update without a full page reload.
4. WHEN the chip is clicked while linked THEN it SHALL offer unlink (existing
   unlink path) with a confirm step.

### Requirement 2 — Duplicates finder (N6)
**User Story:** As a library owner, I want to find duplicate mapsets, so I
can clean wasted disk space from re-downloaded sets.

#### Acceptance Criteria
1. WHEN the user opens the duplicates view THEN the app SHALL group library
   rows whose (artist_title normalized, set-level md5 or beatmap content
   equivalence) collide, excluding legit different-difficulty rows.
2. WHEN a duplicate group is displayed THEN it SHALL show per-group file
   size (when available) and disk path, so the user can decide what to keep.
3. IF a group has rows in multiple install modes (stable vs lazer) THEN the
   view SHALL label the source of each row.
4. WHEN the user selects duplicates THEN export/selection flows SHALL treat
   them like any other selection (no special-casing beyond grouping).

### Requirement 3 — Shortcut help overlay (N8)
**User Story:** As a keyboard user, I want a `?` overlay listing every
shortcut, so the existing shortcuts become discoverable.

#### Acceptance Criteria
1. WHEN the user presses `?` (shift+/) outside an input THEN an overlay SHALL
   list all active shortcuts grouped by scope (list, search, dialog).
2. IF the list is rendered from a single declarative source THEN adding a
   shortcut later updates the overlay automatically (no second hand-written
   list).
3. WHEN the overlay is open THEN Esc closes it and focus returns to the
   previously focused element.

### Requirement 4 — Multi-account (N10)
**User Story:** As a user with several osu! accounts, I want to keep more
than one linked token and switch between them, so played flags can be checked
per account.

#### Acceptance Criteria
1. WHEN a second account is linked THEN the app SHALL store both tokens under
   distinct names without one overwriting the other.
2. IF multiple accounts exist THEN the profile chip SHALL offer a switcher;
   the active account drives `/api/auth/status` and online checks.
3. WHEN switching accounts THEN the app SHALL NOT re-run the online check
   automatically; it SHALL surface that played flags belong to the previous
   account until re-checked.
4. WHEN an account is removed THEN its stored token SHALL be deleted from
   disk (0600 file remains for the rest).
5. Token files SHALL keep the existing permissions and never log secrets.

### Requirement 5 — Relay/API health badge (N11)
**User Story:** As a user, I want to see whether the relay (and my token) are
healthy at a glance, so failed linking attempts stop being mysterious.

#### Acceptance Criteria
1. WHEN the app loads and periodically afterwards THEN the shell SHALL show a
   badge for relay reachability (ok / unreachable / rate-limited) derived
   from cheap existing endpoints.
2. WHEN the token is expired or dead THEN the badge SHALL reflect "relink
   needed" distinctly from "relay unreachable".
3. IF the relay is cold-starting (Render free tier) THEN the badge SHALL
   show a "waking up" state while a probe is in flight rather than an error.
4. Badge polling SHALL be gentle (≥60s interval, single flight, back off
   after failures).

## Out of scope
- N7 themes (optional later), N12 i18n (deferred, large effort).
- Any change to the relay protocol itself (the badge only probes existing
  endpoints).
