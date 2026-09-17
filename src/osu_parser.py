"""Minimal .osu text parser (stdlib only).

Covers what the librarian needs: identity ([Metadata]),
difficulty knobs ([Difficulty]), BPM ([TimingPoints]),
approx length (last HitObject time), mode, ranked info is NOT in .osu.
"""
from __future__ import annotations

import hashlib
import os


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_kv(line: str):
    if ":" not in line:
        return None, None
    k, v = line.split(":", 1)
    return k.strip(), v.strip()


def _parse_bg_line(line: str) -> str:
    """Extract background filename from an [Events] line like 0,0,"bg.jpg",0,0."""
    s = line.strip()
    if not s or s.startswith("//"):
        return ""
    parts = s.split(",")
    if len(parts) < 3 or parts[0].strip() != "0":
        return ""
    # Filename may itself contain commas when quoted, so prefer quoted span.
    if '"' in s:
        try:
            first = s.index('"')
            second = s.index('"', first + 1)
            return s[first + 1:second].strip()
        except ValueError:
            pass
    # Fallback: third CSV field, unquoted.
    return parts[2].strip().strip('"').strip()


def parse_osu_file(path: str) -> dict:
    """Parse one .osu file. Never raises on weird maps — returns best effort."""
    info: dict = {
        "path": path,
        "folder": os.path.basename(os.path.dirname(path)),
        "file": os.path.basename(path),
        "format_version": None,
        "artist": "", "artist_unicode": "", "title": "", "title_unicode": "",
        "creator": "", "version": "", "source": "", "tags": "",
        "beatmap_id": -1, "set_id": -1,
        "mode": 0, "ar": 5.0, "cs": 4.0, "od": 5.0, "hp": 5.0,
        "bpm": 0.0, "length_ms": 0, "drain_ms": 0,
        "md5": "", "bg": "",
    }
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return info
    lines = text.splitlines()
    if lines and lines[0].lower().startswith("osu file format v"):
        try:
            info["format_version"] = int(lines[0].rsplit("v", 1)[1].strip().split()[0])
        except ValueError:
            pass
    section = ""
    uninherited_ms_per_quarter: list[float] = []
    last_object_ms = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section in ("General", "Metadata", "Difficulty"):
            k, v = _parse_kv(line)
            if k is None:
                continue
            kl = k.lower()
            try:
                if section == "General" and kl == "mode":
                    info["mode"] = int(float(v))
                elif section == "Metadata":
                    if kl == "title":
                        info["title"] = v
                    elif kl == "titleunicode":
                        info["title_unicode"] = v
                    elif kl == "artist":
                        info["artist"] = v
                    elif kl == "artistunicode":
                        info["artist_unicode"] = v
                    elif kl == "creator":
                        info["creator"] = v
                    elif kl == "version":
                        info["version"] = v
                    elif kl == "source":
                        info["source"] = v
                    elif kl == "tags":
                        info["tags"] = v
                    elif kl == "beatmapid":
                        info["beatmap_id"] = int(float(v))
                    elif kl == "beatmapsetid":
                        info["set_id"] = int(float(v))
                elif section == "Difficulty":
                    if kl == "approachrate":
                        info["ar"] = float(v)
                    elif kl == "circlesize":
                        info["cs"] = float(v)
                    elif kl == "overalldifficulty":
                        info["od"] = float(v)
                    elif kl == "hpdrainrate":
                        info["hp"] = float(v)
                    elif kl == "draintime" or kl == "drainlength":
                        try:
                            info["drain_ms"] = int(float(v) * 1000)
                        except ValueError:
                            pass
            except ValueError:
                continue
        elif section == "TimingPoints":
            # time,beatLength,meter,...,uninherited,effects
            parts = line.split(",")
            if len(parts) >= 7:
                try:
                    beat_len = float(parts[1])
                    uninherited = parts[6].strip() == "1"
                    if uninherited and beat_len > 0:
                        uninherited_ms_per_quarter.append(beat_len)
                except ValueError:
                    pass
        elif section == "HitObjects":
            parts = line.split(",")
            if len(parts) >= 3:
                try:
                    t = int(float(parts[2]))
                    if t > last_object_ms:
                        last_object_ms = t
                except ValueError:
                    pass
        elif section == "Events":
            # Background: 0,0,"bg.jpg",0,0 (event type 0 only; ignore video/breaks)
            if not info.get("bg"):
                bg = _parse_bg_line(line)
                if bg:
                    info["bg"] = bg
    if uninherited_ms_per_quarter:
        # BPM of first red line (osu! uses the max-BPM section for display in some
        # places; first is the most useful single number for an MVP).
        info["bpm"] = round(60000.0 / uninherited_ms_per_quarter[0], 2)
    info["length_ms"] = last_object_ms
    try:
        info["md5"] = md5_file(path)
    except OSError:
        pass
    return info


MODE_NAMES = {0: "osu", 1: "taiko", 2: "catch", 3: "mania"}


def mode_name(mode: int) -> str:
    return MODE_NAMES.get(int(mode), "osu")
