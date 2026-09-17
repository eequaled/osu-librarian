# osu! Librarian — MVP

Local library manager for **osu!stable** and **osu!lazer** with two switchable modes.

## What it does (MVP)

- **Mode switch (settings):** `stable` | `lazer`
- **Scans all downloaded songs:**
  - stable → `Songs/<set folder>/*.osu` + joins `osu!.db` (metadata, star rating, grades, last-played) + `scores.db` (local scores by MD5 → **played/unplayed**)
  - lazer → hashed `files/` store (`files/*/*/<sha256>` blobs that start with `osu file format`) + optional `client.realm` export (scores live in Realm, not in plain files)
- **Played detection (local):** `played = (md5 in scores.db) OR (osu!.db grade != 0) OR (last_played > 0) OR (unplayed flag == False with scores)`. No scores + grade 0 + never played → **unplayed**.
- **Account link (online check):** osu! API v2 OAuth → `GET /api/v2/beatmaps/{id}/scores/users/{user}/all` per difficulty. Marks online-played for maps with no local score (including maps you don't have locally if you feed a beatmap-id list).
- **Filter + multiselect + view:**
  - filters: text search, played (`all/played/unplayed`), mode (`osu/taiko/catch/mania`), star-rating range, ranked status
  - view: **by mapset** (one row per song, expands to diffs) vs **by difficulty** (one row per diff — like osu! song-select grouping off)
  - multiselect with checkboxes, select-all-filtered, invert, bulk export (txt/json/collection)

## Quick start (no deps)

```bash
python3 main.py scan --mode stable --songs "/path/to/osu!/Songs" --db "/path/to/osu!/osu!.db" --scores "/path/to/osu!/scores.db" --out library.json
python3 main.py report --in library.json --out report.html   # open report.html in browser: filters + checkboxes + mapset/difficulty toggle
python3 main.py ui --in library.json                          # tkinter desktop UI (needs display)
```

Lazer:

```bash
python3 main.py scan --mode lazer --lazer-dir "~/.local/share/osu" --out library.json
```

Online check (needs a free osu! OAuth app: https://osu.ppy.sh/home/account/edit → OAuth):

```bash
python3 main.py check-online --in library.json --client-id XXX --client-secret YYY --user-id YYY --out library.online.json
# then: python3 main.py report --in library.online.json --out report.html
```

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
main.py               CLI (scan / report / ui / check-online)
settings.example.json
src/config.py         settings + OS path auto-detect
src/osu_parser.py     .osu text parser (+ MD5, BPM/length approx)
src/stable_db.py      osu!.db / scores.db binary readers
src/stable_scanner.py stable join: Songs + osu!.db + scores.db
src/lazer_scanner.py  lazer files/ scan + realm-export hook
src/library.py        model + filters + mapset/difficulty grouping + selection
src/osu_api.py        OAuth + bulk online-played check (stdlib only)
src/report.py         static HTML viewer (filters + checkboxes + toggle)
src/app_tk.py         tkinter desktop UI (same filters)
tests/                parser + filter + scanner smoke tests
```

## Next steps (post-MVP)

1. Full Realm read via bundled node helper (call `osu-lazer-db-reader` and import JSON: scores, DateAdded, collections).
2. Proper star-rating calc (osu-tools /difficulty-calculator) instead of osu!.db-cached SR.
3. Write `collection.db`/lazer collections from multiselect; delete/unplayed cleanup with safety confirm.
4. Incremental rescan (mtime cache) for 50k+ map libraries.
