"""Lazer scan: hashed files/ store + optional realm JSON export.

Why not read client.realm directly? It is a Realm database (proprietary
format, magic 'TDBR'/MNTR), NOT SQLite — stdlib sqlite3 cannot open it.
Full local scores/collections need either:
  1. `realm_export` JSON produced by https://github.com/yadPe/osu-lazer-db-reader
     (or any tool dumping Beatmap/BeatmapSet/Score tables), passed as --realm-export
  2. the online API check (check-online), which works regardless.

Without those, this scanner still finds every downloaded difficulty by sniffing
the content-addressable blobs for '.osu' content, which is enough for the
library + unplayed workflow (everything defaults to unplayed locally).
"""
from __future__ import annotations

import json
import os

from .library import Beatmap
from .osu_parser import mode_name


def _is_osu_blob(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(64)
        return head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"osu file format v")
    except OSError:
        return False


def _parse_blob(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    info = {
        "artist": "", "title": "", "creator": "", "version": "",
        "source": "", "tags": "", "beatmap_id": -1, "set_id": -1,
        "mode": 0, "ar": 5.0, "cs": 4.0, "od": 5.0, "hp": 5.0,
        "bpm": 0.0, "length_ms": 0, "bg": "",
    }
    section = ""
    first_bpm = 0.0
    last_t = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section in ("General", "Metadata", "Difficulty") and ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower(); v = v.strip()
            try:
                if section == "General" and k == "mode":
                    info["mode"] = int(float(v))
                elif section == "Metadata":
                    if k == "title": info["title"] = v
                    elif k == "artist": info["artist"] = v
                    elif k == "creator": info["creator"] = v
                    elif k == "version": info["version"] = v
                    elif k == "source": info["source"] = v
                    elif k == "tags": info["tags"] = v
                    elif k == "beatmapid": info["beatmap_id"] = int(float(v))
                    elif k == "beatmapsetid": info["set_id"] = int(float(v))
                elif section == "Difficulty":
                    if k == "approachrate": info["ar"] = float(v)
                    elif k == "circlesize": info["cs"] = float(v)
                    elif k == "overalldifficulty": info["od"] = float(v)
                    elif k == "hpdrainrate": info["hp"] = float(v)
            except ValueError:
                pass
        elif section == "TimingPoints":
            parts = line.split(",")
            if len(parts) >= 7 and not first_bpm:
                try:
                    if parts[6].strip() == "1" and float(parts[1]) > 0:
                        first_bpm = round(60000.0 / float(parts[1]), 2)
                except ValueError:
                    pass
        elif section == "HitObjects":
            parts = line.split(",")
            if len(parts) >= 3:
                try:
                    last_t = max(last_t, int(float(parts[2])))
                except ValueError:
                    pass
        elif section == "Events":
            if not info.get("bg"):
                bg = _parse_blob_bg_line(line)
                if bg:
                    info["bg"] = bg
    info["bpm"] = first_bpm
    info["length_ms"] = last_t
    return info


def _parse_blob_bg_line(line: str) -> str:
    """Background filename from an [Events] line like 0,0,"bg.jpg",0,0."""
    s = line.strip()
    if not s or s.startswith("//"):
        return ""
    parts = s.split(",")
    if len(parts) < 3 or parts[0].strip() != "0":
        return ""
    if '"' in s:
        try:
            first = s.index('"')
            second = s.index('"', first + 1)
            return s[first + 1:second].strip()
        except ValueError:
            pass
    return parts[2].strip().strip('"').strip()


# BeatmapOnlineStatus from ppy/osu osu.Game/Beatmaps/BeatmapOnlineStatus.cs.
STATUS_NAMES = {
    -4: "locally_modified",
    -3: "none",
    -2: "graveyard",
    -1: "wip",
    0: "pending",
    1: "ranked",
    2: "approved",
    3: "qualified",
    4: "loved",
}

# ScoreRank from ppy/osu osu.Game/Scoring/ScoreRank.cs.
RANK_NAMES = {
    -1: "F",
    0: "D",
    1: "C",
    2: "B",
    3: "A",
    4: "S",
    5: "SH",
    6: "X",
    7: "XH",
}

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"})


def _empty_realm() -> dict:
    return {
        "played_ids": set(), "played_md5": set(), "stars": {},
        "grades": {}, "statuses": {}, "dates": {}, "set_files": {},
    }


def _lookup_by_beatmap(d: dict, bid: int, md5: str):
    """Beatmap-keyed lookup: onlineId first, else md5 (case-insensitive)."""
    if not isinstance(d, dict):
        return None
    if bid not in (None, -1):
        for k in (bid, str(bid)):
            if k in d:
                return d[k]
    if md5:
        ml = str(md5).lower()
        for k in (ml, ml.upper()):
            if k in d:
                return d[k]
    return None


def _resolve_bg_hash(bg: str, set_id, set_files: dict) -> str:
    if not bg or not isinstance(set_files, dict):
        return ""
    files = None
    tried = []
    for cand in (set_id, str(set_id)):
        tried.append(cand)
        if cand in set_files:
            files = set_files[cand]
            break
    if files is None:
        try:
            icand = int(str(set_id).strip())
            if icand in set_files:
                files = set_files[icand]
        except (ValueError, TypeError):
            pass
    if not isinstance(files, list) or not files:
        return ""
    bgl = str(bg).lower()
    bgbase = os.path.basename(str(bg)).lower()
    for e in files:
        if not isinstance(e, dict):
            continue
        fn = str(e.get("filename", e.get("file", "")) or "")
        if not fn:
            continue
        exact = (fn.lower() == bgl) or (bool(bgbase) and os.path.basename(fn).lower() == bgbase)
        if exact:
            h = str(e.get("hash", e.get("Hash", "")) or "")
            if h:
                return h
            break
    for e in files:
        if not isinstance(e, dict):
            continue
        fn = str(e.get("filename", e.get("file", "")) or "")
        if os.path.splitext(fn)[1].lower() in IMAGE_EXTS:
            h = str(e.get("hash", e.get("Hash", "")) or "")
            if h:
                return h
    return ""


def normalize_realm_dump(raw: dict) -> dict:
    """Normalize a raw realm-export dict (as produced by tools/realm_export)
    into lookup dicts. BACKWARD COMPAT: played_ids/played_md5/stars keys keep
    their old meaning; grades/statuses/dates/set_files are additive."""
    played_ids: set = set()
    played_md5: set = set()
    stars: dict = {}
    grades: dict = {}
    statuses: dict = {}
    dates: dict = {}
    set_files: dict = {}
    if isinstance(raw, dict):
        best_rank: dict = {}
        for s in raw.get("scores", []):
            if isinstance(s, dict):
                if s.get("beatmapOnlineId") not in (None, -1):
                    try:
                        played_ids.add(int(s["beatmapOnlineId"]))
                    except (ValueError, TypeError):
                        pass
                if s.get("beatmapMd5"):
                    played_md5.add(str(s["beatmapMd5"]).lower())
                # Fallback grade source when beatmaps rows lack `grade`.
                try:
                    r = s.get("rank", None)
                    rank = int(r) if r is not None else None
                except (ValueError, TypeError):
                    rank = None
                if rank is not None:
                    sk = None
                    if s.get("beatmapOnlineId") not in (None, -1):
                        try:
                            sk = int(s["beatmapOnlineId"])
                        except (ValueError, TypeError):
                            sk = None
                    if sk is None and s.get("beatmapMd5"):
                        sk = str(s["beatmapMd5"]).lower()
                    if sk is not None and (sk not in best_rank or rank > best_rank[sk]):
                        best_rank[sk] = rank
        for b in raw.get("beatmaps", []):
            if isinstance(b, dict):
                key = b.get("onlineId", b.get("beatmapOnlineId"))
                md5 = str(b.get("md5", b.get("md5Hash", "")) or "").lower() or None
                ikey = None
                if key not in (None, -1):
                    try:
                        ikey = int(key)
                    except (ValueError, TypeError):
                        ikey = None
                if ikey is not None:
                    try:
                        stars[ikey] = float(b.get("stars", b.get("starRating", 0.0)) or 0.0)
                    except (ValueError, TypeError):
                        stars[ikey] = 0.0
                if b.get("plays", 0):
                    try:
                        if int(b.get("plays", 0) or 0):
                            if ikey is not None:
                                played_ids.add(ikey)
                            if md5:
                                played_md5.add(md5)
                    except (ValueError, TypeError):
                        pass
                # grades / statuses / dates under both onlineId and md5 keys
                grade = str(b.get("grade", "") or "")
                if grade:
                    if ikey is not None:
                        grades[ikey] = grade
                        grades[str(ikey)] = grade
                        played_ids.add(ikey)
                    if md5:
                        grades[md5] = grade
                        played_md5.add(md5)
                st = b.get("status", None)
                sname = b.get("statusName", None)
                if sname is None:
                    try:
                        sname = STATUS_NAMES.get(int(st), "unknown") if st is not None else "unknown"
                    except (ValueError, TypeError):
                        sname = "unknown"
                sname = str(sname or "unknown").lower() or "unknown"
                if ikey is not None:
                    statuses[ikey] = sname
                    statuses[str(ikey)] = sname
                if md5:
                    statuses[md5] = sname
                date_added = str(b.get("dateAdded", b.get("date_added", "")) or "")
                if date_added:
                    if ikey is not None:
                        dates[ikey] = date_added
                        dates[str(ikey)] = date_added
                    if md5:
                        dates[md5] = date_added
        for sk, rank in best_rank.items():
            if sk not in grades:
                letter = RANK_NAMES.get(int(rank), "")
                if letter:
                    grades[sk] = letter
        for k, v in (raw.get("sets", {}) or {}).items():
            if not isinstance(v, list):
                continue
            norm = []
            for e in v:
                if isinstance(e, dict):
                    fn = str(e.get("filename", e.get("file", "")) or "")
                    h = str(e.get("hash", e.get("Hash", "")) or "")
                    if fn:
                        norm.append({"filename": fn, "hash": h})
            # Store under int + string forms so int/str lookups both hit.
            set_files[k] = norm
            try:
                ik = int(str(k).strip())
                set_files[ik] = norm
                set_files[str(ik)] = norm
            except (ValueError, TypeError):
                pass
    return {"played_ids": played_ids, "played_md5": played_md5, "stars": stars,
            "grades": grades, "statuses": statuses, "dates": dates,
            "set_files": set_files}


def load_realm_export(path: str) -> dict:
    """Load a realm-export JSON file (helper output or similar) and normalize
    it. See normalize_realm_dump for the expected shape."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return _empty_realm()
    return normalize_realm_dump(raw)


def parse_lazer_blob(path: str, realm: dict | None = None) -> Beatmap:
    """Parse one hashed .osu blob. Keyed by blob path for caching."""
    import hashlib as _hashlib
    base = {"played_ids": set(), "played_md5": set(), "stars": {},
            "grades": {}, "statuses": {}, "dates": {}, "set_files": {}}
    realm = {**base, **(realm or {})}
    info = _parse_blob(path)
    try:
        with open(path, "rb") as f:
            md5 = _hashlib.md5(f.read()).hexdigest()
    except OSError:
        md5 = path
    bid = int(info.get("beatmap_id", -1) or -1)
    played = (bid in realm["played_ids"]) or (md5.lower() in realm["played_md5"])
    # grades/statuses/dates may also imply played state via md5/onlineId keys
    grade = _lookup_by_beatmap(realm.get("grades", {}), bid, md5) or ""
    status = _lookup_by_beatmap(realm.get("statuses", {}), bid, md5) or "unknown"
    status = str(status or "unknown").lower() or "unknown"
    # Realm None (-3) means "no online status"; display as unknown.
    ranked = "unknown" if status in ("", "none") else status
    date_added = _lookup_by_beatmap(realm.get("dates", {}), bid, md5) or ""
    stars = realm["stars"].get(bid, realm["stars"].get(str(bid), 0.0)) if bid != -1 else 0.0
    try:
        stars = float(stars)
    except (ValueError, TypeError):
        stars = 0.0
    set_key = str(info["set_id"]) if info["set_id"] not in (-1, 0, None) else f"hash:{os.path.basename(path)[:12]}"
    bg = str(info.get("bg", "") or "")
    bg_hash = _resolve_bg_hash(bg, info.get("set_id", -1), realm.get("set_files", {}))
    if not stars:
        # Blob-sniff rows have no realm stars: compute locally
        # (no-op when rosu-pp-py absent).
        try:
            from .difficulty import stars_for_file
            s = stars_for_file(path, int(info.get("mode", 0) or 0))
            if s:
                stars = s
        except Exception:
            pass
    return Beatmap(
        id=md5, set_id=set_key,
        artist=info["artist"], title=info["title"], creator=info["creator"],
        diff=info["version"], source=info["source"], tags=info["tags"],
        mode=int(info["mode"]), mode_name=mode_name(int(info["mode"])),
        ar=float(info["ar"]), cs=float(info["cs"]),
        od=float(info["od"]), hp=float(info["hp"]),
        bpm=float(info["bpm"]), stars=float(stars),
        length_ms=int(info["length_ms"]), beatmap_id=bid,
        ranked=ranked, played_local=played,
        grade=str(grade or ""),
        date_added=str(date_added or ""),
        bg=bg, bg_hash=str(bg_hash or ""),
        score_count=1 if played else 0,
        folder=f"files/{os.path.basename(path)[:2]}/…", origin="lazer",
    )


def scan_lazer(lazer_dir: str = "", realm_export: str = "", _realm: dict | None = None) -> list[Beatmap]:
    files_dir = os.path.join(lazer_dir, "files") if lazer_dir else ""
    blobs: list[str] = []
    if files_dir and os.path.isdir(files_dir):
        for root, _dirs, names in os.walk(files_dir):
            for n in names:
                p = os.path.join(root, n)
                # hashed names are 64-hex; skip anything else cheaply
                if len(n) != 64:
                    # still allow it — cheap sniff decides
                    pass
                if _is_osu_blob(p):
                    blobs.append(p)
    else:
        print(f"[warn] lazer files/ not found under {lazer_dir!r}")

    realm = _empty_realm()
    if _realm is not None:
        realm = _realm
    elif realm_export and os.path.exists(realm_export):
        try:
            realm = load_realm_export(realm_export)
            print(f"[info] realm export: {len(realm['played_ids'])} played ids, "
                  f"{len(realm['played_md5'])} played md5s")
        except Exception as e:
            print(f"[warn] could not read realm export ({e})")
    elif lazer_dir and os.path.exists(os.path.join(lazer_dir, "client.realm")):
        print("[info] client.realm present but not readable with stdlib "
              "(Realm format, not SQLite). Local played=False unless you pass "
              "--realm-export realm.json or run check-online.")

    out: list[Beatmap] = []
    for path in sorted(blobs):
        out.append(parse_lazer_blob(path, realm))
    return out
