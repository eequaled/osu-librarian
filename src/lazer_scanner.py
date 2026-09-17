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
        "bpm": 0.0, "length_ms": 0,
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
    info["bpm"] = first_bpm
    info["length_ms"] = last_t
    return info


def load_realm_export(path: str) -> dict:
    """Expected shape (from osu-lazer-db-reader or similar):
    {"beatmaps": [{onlineId, setOnlineId, md5, stars, status, mode, plays?}],
     "scores": [{beatmapOnlineId, beatmapMd5}]} -> returns lookup dicts."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    played_ids: set = set()
    played_md5: set = set()
    stars: dict = {}
    if isinstance(raw, dict):
        for s in raw.get("scores", []):
            if isinstance(s, dict):
                if s.get("beatmapOnlineId") not in (None, -1):
                    played_ids.add(int(s["beatmapOnlineId"]))
                if s.get("beatmapMd5"):
                    played_md5.add(str(s["beatmapMd5"]).lower())
        for b in raw.get("beatmaps", []):
            if isinstance(b, dict):
                key = b.get("onlineId", b.get("beatmapOnlineId"))
                if key not in (None, -1):
                    stars[int(key)] = float(b.get("stars", b.get("starRating", 0.0)) or 0.0)
                if b.get("plays", 0):
                    if key not in (None, -1):
                        played_ids.add(int(key))
    return {"played_ids": played_ids, "played_md5": played_md5, "stars": stars}


def parse_lazer_blob(path: str, realm: dict | None = None) -> Beatmap:
    """Parse one hashed .osu blob. Keyed by blob path for caching."""
    import hashlib as _hashlib
    realm = realm or {"played_ids": set(), "played_md5": set(), "stars": {}}
    info = _parse_blob(path)
    try:
        with open(path, "rb") as f:
            md5 = _hashlib.md5(f.read()).hexdigest()
    except OSError:
        md5 = path
    bid = int(info.get("beatmap_id", -1) or -1)
    played = (bid in realm["played_ids"]) or (md5.lower() in realm["played_md5"])
    stars = realm["stars"].get(bid, 0.0) if bid != -1 else 0.0
    set_key = str(info["set_id"]) if info["set_id"] not in (-1, 0, None) else f"hash:{os.path.basename(path)[:12]}"
    return Beatmap(
        id=md5, set_id=set_key,
        artist=info["artist"], title=info["title"], creator=info["creator"],
        diff=info["version"], source=info["source"], tags=info["tags"],
        mode=int(info["mode"]), mode_name=mode_name(int(info["mode"])),
        ar=float(info["ar"]), cs=float(info["cs"]),
        od=float(info["od"]), hp=float(info["hp"]),
        bpm=float(info["bpm"]), stars=float(stars),
        length_ms=int(info["length_ms"]), beatmap_id=bid,
        ranked="unknown", played_local=played,
        score_count=1 if played else 0,
        folder=f"files/{os.path.basename(path)[:2]}/…", origin="lazer",
    )


def scan_lazer(lazer_dir: str = "", realm_export: str = "") -> list[Beatmap]:
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

    realm = {"played_ids": set(), "played_md5": set(), "stars": {}}
    if realm_export and os.path.exists(realm_export):
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
