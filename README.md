# osu! Librarian

Local library manager for **osu!stable** and **osu!lazer** with two switchable modes.

## Web app (recommended)

```bash
cp settings.example.json settings.json   # point songs_dir / lazer_dir at your install
python3 main.py web --port 8787          # open http://127.0.0.1:8787
```

Press **scan** once, then browse: text search, played/unplayed, mode, ★ range,
ranked status, 7 sort orders, **by mapset / by difficulty** toggle, checkboxes +
shift-click ranges + `select filtered` / `invert`, detail pane **with background
preview**, top-bar **gamemode selector** (All/osu/taiko/catch/mania, like lazer),
and `export…` (`.json` / `.txt` / stable `collection.db`).

**No setup needed in most cases:** if your library is empty the app offers every
osu! install it finds (stable `%localappdata%/osu!`, wine prefixes, Program
Files, drive roots; lazer `%appdata%/osu`, `~/.local/share/osu`, …) as one-click
**use stable/lazer** buttons. Detection checks marker files (`Songs/` +
`osu!.db`, `files/` + `client.realm`), so empty folders don't count. Manual
override stays available via `settings.json`.

## What it does

- **Mode switch:** `stable` | `lazer` buttons (server tracks the active mode;
  `settings.json` holds the default + paths).
- **Scans all downloaded songs:**
  - stable → `Songs/<set folder>/*.osu` + joins `osu!.db` (metadata, star rating, grades, last-played) + `scores.db` (local scores by MD5 → **played/unplayed**)
  - lazer → hashed `files/` store (`files/*/*/<sha256>` blobs that start with `osu file format`) + optional `client.realm` export (scores live in Realm, not in plain files)
  - lazer **Realm direct read (automatic):** on scan, the bundled helper
    (`tools/realm_export`, needs node once for `npm install`) dumps
    `client.realm` read-only to JSON — real star ratings, local played state +
    grades, ranked status, date added, and background-art resolution. No node?
    It silently falls back to blob-sniffing. Missing stars anywhere are
    computed locally via optional `rosu-pp-py` (`pip install -r requirements.txt`).
- **Played detection (local):** `played = (md5 in scores.db) OR (osu!.db grade != 0) OR (last_played > 0)`. No scores + grade 0 + never played → **unplayed**.
- **Account link (online check):** link button → osu! OAuth → `online check` marks
  `played_online` for maps with no local score (see OAuth setup below).

## Quick start (CLI, no deps)

```bash
python3 main.py scan --mode stable --songs "/path/to/osu!/Songs" --db "/path/to/osu!/osu!.db" --scores "/path/to/osu!/scores.db" --out library.json
python3 main.py report --in library.json --out report.html   # static fallback: filters + checkboxes + mapset/difficulty toggle
python3 main.py ui --in library.json                          # tkinter desktop UI (needs display)
```

Lazer:

```bash
python3 main.py scan --mode lazer --lazer-dir "~/.local/share/osu" --out library.json
```

## OAuth setup (account link)

1. Register an app at <https://osu.ppy.sh/home/account/edit> (OAuth section),
   callback `http://localhost:8080/callback` → note `client_id` + `client_secret`.
2. Put them in `settings.json` → `api` (+ your numeric `user_id` is optional;
   it is auto-detected on link).
3. In the web app: **link account** → open authorize page → paste `?code=` → link.
   CLI alternative: `python3 main.py auth --client-id … --client-secret … --save-token .token.json`,
   then `python3 main.py check-online --in library.json --token …`.

## Caching (why repeat visits are instant)

1. **Scan cache** — `.cache/scan-<mode>.json` + manifest of per-file mtimes.
   The **scan** button always reads the whole library; unchanged files reuse
   cached rows and only new/changed files are re-parsed. db/score changes
   rejoin without re-parsing; `played_online` survives rescans.
2. **Online-score cache** — `.api_cache.json` entries `{played, at}` with a
   **7-day TTL**, crash-safe (persisted every 20), ~0.4s between calls.
3. **HTTP** — `/api/library` has an `ETag` covering file + content state
   (played flags included), so the UI gets `304 Not Modified` when nothing changed.
4. **Client** — debounced search, memoized filtering, prefs + selection in
   `localStorage`, and a windowed list (only visible rows in the DOM).

## API (for hackers)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/status` | mode, path checks, counts, cache + job + auth state |
| GET | `/api/library` | `{version, maps}` + ETag → `304` |
| POST | `/api/scan` | `{mode?, fresh?}` → `{job_id}` (background, incremental) |
| POST | `/api/mode` | `{mode}` switch without scanning |
| GET | `/api/jobs/:id` | `{state, done, total, error?}` |
| GET | `/api/auth/url` | authorize URL from settings creds |
| POST | `/api/auth/code` | `{code}` → token stored server-side (`0600`) |
| GET | `/api/auth/status` | `{linked, user_id}` |
| POST | `/api/online-check` | background bulk score check → `{job_id}` |
| POST | `/api/export` | `{ids, format: json\|txt\|collection}` → download |
| GET | `/api/detect` | installs found on this machine |
| POST | `/api/use-install` | `{kind, path}` adopt an install, persist to settings |

Settings file `settings.json` stores mode + paths + api creds (see `settings.example.json`). The UI can switch modes without CLI flags.

## Research notes (how osu! actually works)

**osu!stable (Windows `%localappdata%/osu!`):**
- `Songs/<BeatmapSetID> <Artist> - <Title>/*.osu` — one text file per difficulty (`[Metadata]` BeatmapID/BeatmapSetID, `[Difficulty]` AR/CS/OD/HP, `[TimingPoints]` → BPM).
- `osu!.db` — binary cache of every installed diff: grades per mode (byte, 0 = unplayed), `IsUnplayed` bool, `LastPlayed` ticks, star-rating tables per mode (Int→Float pairs since db v20250107), ranked status, folder + `.osu` filename, MD5.
- `scores.db` — binary: `md5 → [scores]` (mode, 300/100/50/geki/katu/miss, score, combo, mods, timestamp, online ID). **Join key everywhere is the beatmap MD5.**
- Song select: Group (No grouping / By Difficulty / Artist / Recently Played / Collections / BPM / Creator / Date Added / Length / Mode / Rank Achieved / Title / Favourites / My Maps / Ranked Status) + Sort (Artist/BPM/Creator/Rank/Title/…) + search bar filters (`ar=`, `stars>=`, `mode=`, `status=`, `played`, `unplayed=`, `length=`, `bpm=`, …).

**osu!lazer (`%appdata%/osu`, `~/.local/share/osu`, `~/Library/Application Support/osu`):**
- No `Songs/` folder. Content-addressable store: `files/<first>/<first-two>/<sha256>` + mappings in **`client.realm`** (Realm DB, not SQLite — can't be read with `sqlite3`/stdlib).
- Realm tables: `BeatmapSet` (OnlineID, Status, DateAdded, Files, Beatmaps), `Beatmap` (OnlineID, DifficultyName, StarRating, BPM, Length, MD5Hash, Ruleset, Status), `Score` (BeatmapInfo link, BeatmapHash, Ruleset, OnlineID, stats). Played state = existence of `Score` rows for that `Beatmap`.
- MVP lazer scan reads the hashed blobs directly (zero-dep) and treats Realm scores as optional: export with [`osu-lazer-db-reader`](https://github.com/yadPe/osu-lazer-db-reader) (`npx`) → `--realm-export realm.json`, or rely on the online API check.
- Song select in lazer mirrors stable (search/group/sort), same filter syntax.

**osu! API v2 (`https://osu.ppy.sh/api/v2`):**
- OAuth: register app → `client_id/secret` → Authorization-Code flow (`identify public` scopes) → Bearer token.
- Relevant: `GET /beatmaps/{beatmap}/scores/users/{user}/all`, `GET /beatmaps/{beatmap}/scores/users/{user}`, `GET /users/{user}/scores/{best|firsts|recent}`, `GET /beatmaps/lookup?checksum=<md5>`. Rate-limit; cache results (MVP does, in `.api_cache.json`).

## Layout

```
main.py               CLI (scan / report / ui / web / auth / check-online)
settings.example.json
src/config.py         settings + OS path auto-detect
src/osu_parser.py     .osu text parser (+ MD5, BPM/length approx)
src/stable_db.py      osu!.db / scores.db binary readers
src/stable_scanner.py stable join: Songs + osu!.db + scores.db (+per-file parse/rejoin)
src/lazer_scanner.py  lazer files/ scan + realm-export hook (+per-blob parse)
src/library.py        model + filters + mapset/difficulty grouping + selection
src/osu_api.py        OAuth + bulk online-played check with TTL cache (stdlib only)
src/cache.py          incremental scan cache (fingerprint + manifest + ETag version)
src/jobs.py           background job registry with progress
src/server.py         local HTTP API + static frontend (stdlib only)
src/export.py         selection exporters (json/txt/collection.db)
src/report.py         static HTML fallback viewer
src/app_tk.py         tkinter desktop UI
web/                  no-build frontend (index.html, styles.css, api/store/views/auth/bulk/main.js)
tests/                `python3 -m unittest discover -s tests` (python + node store checks)
docs/webui-spec.md    build spec this app was implemented from
```

## Tests

```bash
python3 -m unittest discover -s tests   # 20 tests: parsers, filters, cache/TTL,
                                        # jobs, API server, CLI, web e2e, store.js via node
```

## Next steps (post-MVP)

1. Full Realm read via bundled node helper (call `osu-lazer-db-reader` and import JSON: scores, DateAdded, collections).
2. Proper star-rating calc (osu-tools /difficulty-calculator) instead of osu!.db-cached SR.
3. Write `collection.db`/lazer collections from multiselect; delete/unplayed cleanup with safety confirm.
4. Incremental rescan (mtime cache) for 50k+ map libraries.
