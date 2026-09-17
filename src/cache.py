"""Incremental scan cache.

The full parse of 50k .osu files takes minutes; file mtimes don't change
between visits. So we fingerprint the library (per-file mtime+size plus the
binary db mtimes) and on rescan only re-parse new/changed files, reusing
cached entries for the rest. `version` (hash of fingerprint + counts) doubles
as the HTTP ETag for /api/library.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os

CACHE_DIR = ".cache"


def cache_paths(mode: str) -> tuple[str, str]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return (os.path.join(CACHE_DIR, f"scan-{mode}.json"),
            os.path.join(CACHE_DIR, "manifest.json"))


def _stat(path: str):
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def fingerprint_stable(songs_dir: str, osu_db: str, scores_db: str) -> dict:
    files: dict[str, list] = {}
    if songs_dir and os.path.isdir(songs_dir):
        for path in glob.glob(os.path.join(glob.escape(songs_dir), "*", "*.osu")):
            s = _stat(path)
            if s:
                files[os.path.relpath(path, songs_dir)] = s
    return {"files": files, "db": _stat(osu_db or "") or [],
            "scores": _stat(scores_db or "") or []}


def fingerprint_lazer(lazer_dir: str) -> dict:
    files: dict[str, list] = {}
    files_dir = os.path.join(lazer_dir, "files") if lazer_dir else ""
    if files_dir and os.path.isdir(files_dir):
        for root, _dirs, names in os.walk(files_dir):
            for n in names:
                p = os.path.join(root, n)
                s = _stat(p)
                if s:
                    files[os.path.relpath(p, files_dir)] = s
    return {"files": files}


def fingerprint(mode: str, **kw) -> dict:
    if mode == "lazer":
        return fingerprint_lazer(kw.get("lazer_dir", ""))
    return fingerprint_stable(kw.get("songs_dir", ""), kw.get("osu_db", ""),
                              kw.get("scores_db", ""))


def diff_fingerprints(old: dict, new: dict) -> tuple[set, set, set, bool]:
    """(unchanged, changed_or_new, deleted, dbs_changed) between two fingerprints."""
    of, nf = old.get("files", {}), new.get("files", {})
    unchanged = {k for k, v in nf.items() if of.get(k) == v}
    changed = set(nf) - unchanged
    deleted = set(of) - set(nf)
    dbs_changed = old.get("db") != new.get("db") or old.get("scores") != new.get("scores")
    return unchanged, changed, deleted, dbs_changed


def version_for(maps_count: int, fp: dict, tag: str = "") -> str:
    """ETag for /api/library. tag covers row content (e.g. online-check flags)
    so content changes bust the cache even when the file fingerprint matches."""
    h = hashlib.sha1()
    h.update(str(maps_count).encode())
    h.update(tag.encode())
    h.update(json.dumps(fp, sort_keys=True).encode())
    return h.hexdigest()[:16]


def load_scan(mode: str) -> tuple[list, list[dict], dict]:
    """Returns (keys, rows, fingerprint). keys[i] is the file relpath for rows[i]
    (None for db-only entries with no file on disk)."""
    scan_path, manifest_path = cache_paths(mode)
    try:
        with open(scan_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], [], {}
    if isinstance(data, list):  # legacy bare-rows format
        rows = data
        keys = [None] * len(rows)
    else:
        rows = data.get("rows", [])
        keys = data.get("keys", [None] * len(rows))
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifests = json.load(f)
    except (OSError, ValueError):
        manifests = {}
    return keys, rows, manifests.get(mode, {})


def save_scan(mode: str, keys: list, rows: list[dict], fp: dict) -> None:
    scan_path, manifest_path = cache_paths(mode)
    with open(scan_path, "w", encoding="utf-8") as f:
        json.dump({"keys": keys, "rows": rows}, f)


def save_scan(mode: str, keys: list, rows: list[dict], fp: dict) -> None:
    scan_path, manifest_path = cache_paths(mode)
    with open(scan_path, "w", encoding="utf-8") as f:
        json.dump({"keys": keys, "rows": rows}, f)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifests = json.load(f)
    except (OSError, ValueError):
        manifests = {}
    manifests[mode] = fp
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifests, f, indent=1)
