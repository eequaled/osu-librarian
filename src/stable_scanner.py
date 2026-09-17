"""Stable scan: Songs/*.osu + osu!.db + scores.db joined on MD5."""
from __future__ import annotations

import glob
import os

from .library import Beatmap
from .osu_parser import mode_name, parse_osu_file
from .stable_db import RANKED_NAMES, read_osu_db, read_scores_db


def _played_from_db(db_grade_any: bool, unplayed: bool, last_played: int, n_scores: int) -> bool:
    if n_scores > 0:
        return True
    if db_grade_any:
        return True
    if last_played and last_played > 0:
        return True
    return False


def load_db_maps(osu_db_path: str) -> dict:
    """osu!.db MD5 -> row (empty dict when missing/unreadable)."""
    if osu_db_path and os.path.exists(osu_db_path):
        try:
            _, maps = read_osu_db(osu_db_path)
            return {m.md5.lower(): m for m in maps}
        except Exception as e:
            print(f"[warn] could not read osu!.db ({e}); falling back to .osu only")
    return {}


def load_score_map(scores_path: str) -> dict:
    """scores.db MD5 -> [scores] (empty dict when missing/unreadable)."""
    if scores_path and os.path.exists(scores_path):
        try:
            _, by_md5 = read_scores_db(scores_path)
            return {k.lower(): v for k, v in by_md5.items()}
        except Exception as e:
            print(f"[warn] could not read scores.db ({e}); played flags come from osu!.db only")
    return {}


def parse_stable_file(path: str, db_by_md5: dict, scores_by_md5: dict) -> Beatmap:
    """Parse one .osu file + join db rows. Keyed by file path for caching."""
    from .stable_db import GRADE_NAMES
    p = parse_osu_file(path)
    md5 = (p["md5"] or "").lower()
    db = db_by_md5.get(md5) if md5 else None
    scores = scores_by_md5.get(md5, []) if md5 else []
    grade_any = bool(db and any(g for g in db.grades))
    played = _played_from_db(
        grade_any, db.unplayed if db else True, db.last_played if db else 0,
        len(scores),
    )
    grade = ""
    if db:
        g = db.grades[p["mode"]] if 0 <= p["mode"] < 4 else 0
        grade = GRADE_NAMES.get(g, "")
    stars = db.stars if db and db.stars else 0.0
    ranked = RANKED_NAMES.get(db.ranked, "unknown") if db else "unknown"
    set_key = str(p["set_id"]) if p["set_id"] not in (-1, 0, None) else f"local:{p['folder']}"
    return Beatmap(
        id=md5 or path,
        set_id=set_key,
        artist=p["artist"], title=p["title"], creator=p["creator"],
        diff=p["version"], source=p["source"], tags=p["tags"],
        mode=p["mode"], mode_name=mode_name(p["mode"]),
        ar=p["ar"] if not db else db.ar, cs=p["cs"] if not db else db.cs,
        od=p["od"] if not db else db.od, hp=p["hp"] if not db else db.hp,
        bpm=p["bpm"], stars=stars, length_ms=p["length_ms"],
        beatmap_id=p["beatmap_id"], ranked=ranked,
        played_local=played, grade=grade,
        last_played=db.last_played if db else 0,
        score_count=len(scores), folder=p["folder"], origin="stable",
    )


def scan_stable(songs_dir: str = "", osu_db_path: str = "", scores_path: str = "") -> list[Beatmap]:
    db_by_md5: dict = load_db_maps(osu_db_path)
    scores_by_md5: dict = load_score_map(scores_path)

    files: list[str] = []
    if songs_dir and os.path.isdir(songs_dir):
        files = glob.glob(os.path.join(glob.escape(songs_dir), "*", "*.osu"))
    else:
        print("[warn] Songs folder not found; returning osu!.db entries only" if db_by_md5 else "[warn] no Songs folder and no osu!.db")

    out: list[Beatmap] = []
    seen: set[str] = set()
    for path in sorted(files):
        out.append(parse_stable_file(path, db_by_md5, scores_by_md5))
        md5 = out[-1].id.lower()
        if len(md5) == 32:
            seen.add(md5)

    # db entries with no file on disk (deleted Songs but stale cache) — keep flagged
    for md5, db in db_by_md5.items():
        if md5 in seen:
            continue
        scores = scores_by_md5.get(md5, [])
        grade_any = any(g for g in db.grades)
        played = _played_from_db(grade_any, db.unplayed, db.last_played, len(scores))
        from .stable_db import GRADE_NAMES
        g = db.grades[db.mode] if 0 <= db.mode < 4 else 0
        out.append(Beatmap(
            id=md5, set_id=f"missing:{db.folder}",
            artist=db.artist, title=db.title, creator=db.creator,
            diff=db.difficulty, source=db.source, tags=db.tags,
            mode=db.mode, mode_name=mode_name(db.mode),
            ar=db.ar, cs=db.cs, od=db.od, hp=db.hp,
            bpm=0.0, stars=db.stars, length_ms=db.total_ms,
            beatmap_id=db.beatmap_id,
            ranked=RANKED_NAMES.get(db.ranked, "unknown"),
            played_local=played, grade=GRADE_NAMES.get(g, ""),
            last_played=db.last_played, score_count=len(scores),
            folder=db.folder + " (missing .osu)", origin="stable",
        ))
    return out
