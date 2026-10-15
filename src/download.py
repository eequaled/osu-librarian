"""Bulk download + always-in-a-collection (stdlib only).

Mirrors (verified Sep 2026):
  primary  GET https://osu.direct/api/md5/<md5>   -> {"ParentSetID":..,"BeatmapID":..}
  fallback GET https://catboy.best/api/md5/<md5>  -> same shape
  bytes    GET https://osu.direct/api/d/<set>     (browser UA + Referer, GET not HEAD)

Dead (do not use as primary): nerinyan.moe (HTTP 530), chimu.moe (DNS dead),
kitsu/beatconnect/sayobot (401/status:-1).

Rate rule (measured): max 2 lookup workers, >=0.5s pacing between lookups,
>=1.0s between downloads, 4 retries with `2 + attempt*3`s backoff, honor
Retry-After (cap 60s). This module is sequential by default (safest); the
CLI accepts --jobs for compat but caps at 2 and still paces.

Collection invariant: one run = one collection minimum. Content = requested
MD5s (not just downloaded bytes). Every run emits collection.db +
mapping.json + missing report into the run dir.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from .stable_db import Cursor

OSU_DIRECT_MD5 = "https://osu.direct/api/md5/{md5}"
CATBOY_MD5 = "https://catboy.best/api/md5/{md5}"
OSU_DIRECT_DL = "https://osu.direct/api/d/{set_id}"

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = "https://osu.direct/"

LOOKUP_DELAY_S = 0.5
DOWNLOAD_DELAY_S = 1.0
MAX_RETRIES = 4
RETRY_AFTER_CAP_S = 60.0
MAX_SETS_PER_RUN = 2000
SKIP_EXISTS_BYTES = 100 * 1024
REJECT_BODY_BYTES = 50 * 1024

_MD5_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")
_SET_URL_RE = re.compile(r"beatmapsets/(\d+)", re.IGNORECASE)
_MAP_URL_RE = re.compile(r"beatmaps/(\d+)", re.IGNORECASE)


# ---- collection.db parsing (reuse stable_db.Cursor) ----

def parse_collection_db(path: str) -> list[dict]:
    """Parse stable collection.db -> [{"name": str, "md5s": [str]}].

    Format: u32 version + u32 num_collections + per-collection
    { osu-string name, u32 num_maps, osu-string md5 x N }.
    """
    with open(path, "rb") as f:
        buf = f.read()
    c = Cursor(buf)
    _version = c.u32()
    n = c.u32()
    if n > 10000:
        raise ValueError(f"implausible collection count {n}")
    out: list[dict] = []
    for _ in range(n):
        name = c.string()
        m = c.u32()
        if m > 200000:
            raise ValueError(f"implausible map count {m}")
        md5s = [c.string() for _ in range(m)]
        out.append({"name": name, "md5s": md5s})
    return out


def write_multi_collection_db(collections: list[tuple[str, list[str]]]) -> bytes:
    """Stable collection.db with N collections (for --keep-separate)."""
    from .export import _enc_string
    out = bytearray()
    out += struct.pack("<I", 20150203)
    out += struct.pack("<I", len(collections))
    for name, md5s in collections:
        out += _enc_string(name)
        out += struct.pack("<I", len(md5s))
        for m in md5s:
            out += _enc_string(m)
    return bytes(out)


# ---- id / md5 list parsing ----

def parse_id_list(text: str) -> dict:
    """Parse pasted/file text -> {"sets": [...], "maps": [...], "md5s": [...]}.

    Accepts osu! URLs (beatmapsets/<id>, beatmaps/<id>), bare integer ids,
    and 32-char md5s, one per line or comma/space separated.
    """
    sets: list[int] = []
    maps: list[int] = []
    md5s: list[str] = []
    seen_sets: set[int] = set()
    seen_maps: set[int] = set()
    seen_md5: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for m in _SET_URL_RE.findall(line):
            try:
                sid = int(m)
            except ValueError:
                continue
            if sid not in seen_sets:
                seen_sets.add(sid)
                sets.append(sid)
        for m in _MAP_URL_RE.findall(line):
            try:
                bid = int(m)
            except ValueError:
                continue
            # a /beatmaps/ URL inside a /beatmapsets/ URL line would double
            # match; only treat as map id when no set id on the same line.
            if f"beatmapsets/" not in line.lower() and bid not in seen_maps:
                seen_maps.add(bid)
                maps.append(bid)
        for m in _MD5_RE.findall(line):
            low = m.lower()
            if low not in seen_md5:
                seen_md5.add(low)
                md5s.append(low)
        # bare integer ids (a line that is just digits)
        if re.fullmatch(r"\d+", line):
            try:
                num = int(line)
            except ValueError:
                continue
            # Heuristic: beatmapset ids and beatmap ids share a namespace in
            # pasted lists; treat bare numbers as SET ids only when they did
            # not already parse as a URL above on this line. Callers that
            # know they have map ids should use URLs or --maps.
            if num not in seen_sets:
                seen_sets.add(num)
                sets.append(num)
    return {"sets": sets, "maps": maps, "md5s": md5s}


def load_id_file(path: str) -> dict:
    """Load .txt/.json id list file -> same shape as parse_id_list."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    if path.lower().endswith(".json"):
        try:
            data = json.loads(raw or "null")
        except ValueError as e:
            raise ValueError(f"bad json in {path}: {e}")
        if isinstance(data, dict):
            sets = [int(x) for x in data.get("sets", []) or []]
            maps = [int(x) for x in data.get("maps", []) or []]
            md5s = [str(x).lower() for x in data.get("md5s", []) or []]
            return {"sets": sets, "maps": maps, "md5s": md5s}
        if isinstance(data, list):
            return parse_id_list("\n".join(str(x) for x in data))
        raise ValueError(f"bad json shape in {path}")
    return parse_id_list(raw)


# ---- mirror lookups ----

def _retry_after_secs(exc) -> float | None:
    try:
        hdrs = getattr(exc, "headers", None)
        raw = hdrs.get("Retry-After") if hdrs is not None else None
        if raw is None:
            return None
        secs = float(str(raw).strip())
        if secs != secs or secs < 0:
            return None
        return min(secs, RETRY_AFTER_CAP_S)
    except (TypeError, ValueError, AttributeError):
        return None


def _fetch_json(url: str, timeout: float = 30.0) -> dict | None:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": BROWSER_UA,
        "Referer": REFERER,
    })
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
                try:
                    data = json.loads(body or "null")
                except ValueError:
                    return None
                return data if isinstance(data, dict) else None
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                if attempt < MAX_RETRIES - 1:
                    delay = _retry_after_secs(e)
                    if delay is None:
                        delay = 2 + attempt * 3
                    time.sleep(delay)
                    continue
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 + attempt * 3)
                continue
            return None
    return None


def _extract_ids(data: dict) -> tuple[int | None, int | None, str | None]:
    if not isinstance(data, dict):
        return None, None, None
    sid = None
    for k in ("ParentSetID", "parent_set_id", "beatmapset_id", "set_id", "SetID"):
        if data.get(k) is not None:
            try:
                sid = int(data[k])
                break
            except (TypeError, ValueError):
                continue
    bid = None
    for k in ("BeatmapID", "beatmap_id", "id", "ID"):
        if data.get(k) is not None:
            try:
                bid = int(data[k])
                break
            except (TypeError, ValueError):
                continue
    # beatmapset-level responses reuse "id" for the SET id; if both landed on
    # the same value, the map id is unknown.
    diff = data.get("DiffName") or data.get("diff") or data.get("version")
    if not isinstance(diff, str):
        diff = None
    if sid is not None and bid is not None and sid == bid:
        bid = None
    if sid is None or sid <= 0:
        return None, None, diff
    return sid, bid, diff


def lookup_md5(md5: str) -> tuple[int | None, int | None, str | None]:
    """Resolve one md5 -> (set_id|None, beatmap_id|None, diff|None).

    osu.direct primary, catboy.best fallback. None set_id after both fail
    (deleted/graveyard maps) — callers report these as missing, never silent.
    """
    md5 = (md5 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", md5):
        return None, None, None
    data = _fetch_json(OSU_DIRECT_MD5.format(md5=md5))
    sid, bid, diff = _extract_ids(data or {})
    if sid:
        return sid, bid, diff
    data = _fetch_json(CATBOY_MD5.format(md5=md5))
    return _extract_ids(data or {})


def dedupe(seq: list) -> list:
    seen: set = set()
    out: list = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def resolve_md5s(md5s: list[str], lookup_fn=None,
                 delay_s: float = LOOKUP_DELAY_S,
                 progress=None) -> tuple[dict, list[str]]:
    """Resolve md5s -> (md5_info, missing). Dedupes before lookup.

    md5_info: {md5: {"set": int, "map": int|None, "diff": str|None}}.
    Respects >=0.5s pacing between network lookups.
    """
    fn = lookup_fn or lookup_md5
    unique = dedupe([m.lower() for m in md5s if m])
    info: dict[str, dict] = {}
    missing: list[str] = []
    for i, md5 in enumerate(unique):
        if i > 0 and delay_s > 0:
            time.sleep(delay_s)
        try:
            sid, bid, diff = fn(md5)
        except Exception:
            sid, bid, diff = None, None, None
        if sid:
            info[md5] = {"set": sid, "map": bid, "diff": diff}
        else:
            missing.append(md5)
        if progress:
            try:
                progress(i + 1, len(unique))
            except Exception:
                pass
    return info, missing


# ---- downloads ----

def download_set(set_id: int, outdir: str, timeout: float = 120.0) -> str:
    """Download one set -> "ok" | "exists" | "FAIL: <reason>".

    Skips when <set_id>.osz already exists and is >100KB. Writes via .part +
    rename; rejects bodies <50KB (error pages). GET only (HEAD -> 403).
    """
    os.makedirs(outdir, exist_ok=True)
    dest = os.path.join(outdir, f"{set_id}.osz")
    try:
        if os.path.isfile(dest) and os.path.getsize(dest) > SKIP_EXISTS_BYTES:
            return "exists"
    except OSError:
        pass
    part = dest + ".part"
    req = urllib.request.Request(
        OSU_DIRECT_DL.format(set_id=set_id),
        headers={"User-Agent": BROWSER_UA, "Referer": REFERER,
                 "Accept": "application/octet-stream"},
        method="GET")
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if len(body) < REJECT_BODY_BYTES:
                return f"FAIL: body too small ({len(body)} bytes)"
            if body[:2] != b"PK":
                return "FAIL: not a zip (no PK header)"
            with open(part, "wb") as f:
                f.write(body)
            os.replace(part, dest)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                if attempt < MAX_RETRIES - 1:
                    delay = _retry_after_secs(e)
                    if delay is None:
                        delay = 2 + attempt * 3
                    time.sleep(delay)
                    continue
            return f"FAIL: HTTP {e.code}"
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 + attempt * 3)
                continue
            return f"FAIL: {type(e).__name__}: {e}"
    return "FAIL: retries exhausted"


# ---- run orchestration (collection invariant) ----

def default_run_name(source_name: str | None, md5s: list[str]) -> str:
    if source_name:
        return source_name
    digest = hashlib.sha1("\n".join(sorted(md5s)).encode()).hexdigest()[:8]
    day = datetime.date.today().isoformat()
    return f"Librarian {day} {digest}"


def run_download(*, md5s: list[str], outdir: str,
                 name: str | None = None,
                 source_name: str | None = None,
                 source_collections: list[dict] | None = None,
                 extra_sets: list[int] | None = None,
                 only_list: bool = False,
                 lookup_fn=None, download_fn=None,
                 lookup_delay_s: float = LOOKUP_DELAY_S,
                 download_delay_s: float = DOWNLOAD_DELAY_S,
                 merge: bool = True,
                 progress=None) -> dict:
    """Resolve + download one run. Always emits collection.db + mapping.json.

    Returns the manifest dict (also written to <outdir>/mapping.json).
    Collection content = ALL requested MD5s (missing bytes stay listed).
    extra_sets (from --sets / beatmapset URLs) download without entering
    the collection (no md5 known); they are listed under manifest "sets_extra".
    """
    from .export import to_collection_db_with_counts

    md5s = dedupe([m.lower() for m in (md5s or []) if m])
    extra_sets = dedupe([int(s) for s in (extra_sets or []) if int(s) > 0])
    if len(set(md5s)) == 0 and not extra_sets:
        raise ValueError("nothing to download: no md5s or set ids given")
    os.makedirs(outdir, exist_ok=True)
    sets_dir = os.path.join(outdir, "sets")
    os.makedirs(sets_dir, exist_ok=True)

    run_name = name or default_run_name(source_name, md5s)

    md5_info, missing = resolve_md5s(md5s, lookup_fn=lookup_fn,
                                     delay_s=lookup_delay_s,
                                     progress=(lambda d, t: progress("resolve", d, t)) if progress else None)
    set_ids = dedupe([md5_info[m]["set"] for m in md5_info])
    all_sets = dedupe([*set_ids, *extra_sets])
    if len(all_sets) > MAX_SETS_PER_RUN:
        raise ValueError(f"too many sets ({len(all_sets)} > {MAX_SETS_PER_RUN}); split the run")

    # --- collection.db (invariant: always emitted, full requested list) ---
    if source_collections and len(source_collections) > 1 and not merge:
        multi = [(c.get("name", run_name), c.get("md5s", []))
                 for c in source_collections]
        payload = write_multi_collection_db(multi)
        stats = {"total": sum(len(m[1]) for m in multi),
                 "exported": sum(len(m[1]) for m in multi), "skipped": 0}
    else:
        rows = [{"id": m} for m in md5s]
        payload, stats = to_collection_db_with_counts(rows, run_name)
    with open(os.path.join(outdir, "collection.db"), "wb") as f:
        f.write(payload)

    # --- downloads ---
    dl_status: dict[str, str] = {}
    if not only_list:
        fn = download_fn or (lambda sid, d: download_set(sid, d))
        for i, sid in enumerate(all_sets):
            if i > 0 and download_delay_s > 0:
                time.sleep(download_delay_s)
            try:
                st = fn(sid, sets_dir)
            except Exception as e:  # never abort the run on one set
                st = f"FAIL: {type(e).__name__}: {e}"
            dl_status[str(sid)] = st
            if progress:
                try:
                    progress("download", i + 1, len(all_sets))
                except Exception:
                    pass

    manifest = {
        "name": run_name,
        "source": source_name or "",
        "collections": source_collections or [{"name": run_name, "md5s": md5s}],
        "diff_count": len(md5s),
        "set_ids": set_ids,
        "sets_extra": extra_sets,
        "all_sets": all_sets,
        "md5_info": md5_info,
        "missing": missing,
        "download": dl_status,
        "only_list": only_list,
        "export_stats": stats,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(outdir, "mapping.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    with open(os.path.join(outdir, "missing.txt"), "w", encoding="utf-8") as f:
        for m in missing:
            f.write(m + "\n")
    return manifest


def build_lazer_import(run_dir: str, import_dir: str) -> dict:
    """Build fake-stable layout for lazer import: {Songs/*.osz, collection.db}.

    Never writes to the real lazer_dir. Returns {"osz": N, "dir": import_dir}.
    Also writes import-INSTRUCTIONS.txt with the 4-step flow.
    """
    songs = os.path.join(import_dir, "Songs")
    os.makedirs(songs, exist_ok=True)
    src_db = os.path.join(run_dir, "collection.db")
    if os.path.isfile(src_db):
        shutil.copyfile(src_db, os.path.join(import_dir, "collection.db"))
    n = 0
    for base in (os.path.join(run_dir, "sets"), run_dir):
        if not os.path.isdir(base):
            continue
        for fn in sorted(os.listdir(base)):
            if not fn.lower().endswith(".osz"):
                continue
            src = os.path.join(base, fn)
            dst = os.path.join(songs, fn)
            if os.path.abspath(src) == os.path.abspath(dst):
                continue
            try:
                if not os.path.isfile(dst):
                    shutil.copyfile(src, dst)
                n += 1
            except OSError:
                pass
    instructions = (
        "Lazer import (fake-stable trick):\n"
        "1. In lazer, open Settings -> open the First-time-setup / Import screen.\n"
        "2. Choose 'previous osu! install' and point it at this folder:\n"
        f"   {import_dir}\n"
        "   (it must contain ./Songs/*.osz and ./collection.db).\n"
        "3. Click Import content: maps + collection land in lazer.\n"
        "4. Stable handoff instead? Drag the .osz files into stable, then merge\n"
        "   collection.db via CollectionManager (never overwrite your live file).\n"
    )
    with open(os.path.join(import_dir, "import-INSTRUCTIONS.txt"), "w",
              encoding="utf-8") as f:
        f.write(instructions)
    return {"osz": n, "dir": import_dir}
