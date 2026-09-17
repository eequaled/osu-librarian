"""Local web server: JSON API + static frontend. Stdlib only.

Endpoints (see docs/webui-spec.md for the contract):
  GET  /                        web/index.html (+ static assets)
  GET  /api/status
  GET  /api/library             (ETag / If-None-Match -> 304)
  POST /api/scan                {mode?, fresh?} -> {job_id}
  GET  /api/jobs/:id
  GET  /api/auth/url
  POST /api/auth/code           {code} -> {ok, user_id}
  GET  /api/auth/status         {linked, user_id}
  POST /api/online-check        {} -> {job_id}
  POST /api/export              {ids, format} -> file download
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cache as cachemod
from .config import Settings, load_settings
from .jobs import JobRegistry
from .library import from_dict_list, summarize, to_dict_list

TOKEN_PATH = ".token.json"
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

registry = JobRegistry()
_settings_lock = threading.Lock()
_current_mode: str | None = None  # active mode for this server process


def get_mode() -> str:
    """Active mode: last scanned/switched mode, else settings.json."""
    global _current_mode
    if _current_mode not in ("stable", "lazer"):
        _current_mode = load_settings().mode
    return _current_mode


def set_mode(mode: str) -> str:
    global _current_mode
    _current_mode = mode
    return mode


def _version(rows: list[dict], fp: dict) -> str:
    played = sum(1 for r in rows if r.get("played_local") or r.get("played_online"))
    return cachemod.version_for(len(rows), fp, f"played={played}")


def library_paths(s: Settings, mode: str) -> dict:
    s = s.resolved()
    if mode == "lazer":
        return {"lazer_dir": s.lazer_dir, "realm_export": s.realm_export}
    songs = s.songs_dir or (os.path.join(s.stable_dir, "Songs") if s.stable_dir else "")
    return {"songs_dir": songs, "osu_db": s.osu_db, "scores_db": s.scores_db}


def _load_token() -> dict:
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_token(tok: dict) -> None:
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(tok, f)
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass


def valid_token(settings: Settings) -> str:
    """Usable access token or '' (refreshes server-side when possible)."""
    tok = _load_token()
    if not tok.get("access_token"):
        return ""
    age = time.time() - float(tok.get("obtained_at", 0))
    if age < int(tok.get("expires_in", 0)) - 60:
        return tok["access_token"]
    if tok.get("refresh_token") and settings.api.client_id and settings.api.client_secret:
        from .osu_api import refresh_token as _rt
        try:
            new = _rt(settings.api.client_id, settings.api.client_secret,
                      tok["refresh_token"])
            new["obtained_at"] = time.time()
            if "refresh_token" not in new:
                new["refresh_token"] = tok["refresh_token"]
            _save_token(new)
            return new.get("access_token", "")
        except Exception:
            return ""
    return tok.get("access_token", "")


# ---- scans ----

def _carry_online(old_rows: list[dict], new_rows: list[dict]) -> None:
    """Keep played_online flags across re-parses (online state isn't on disk)."""
    online = {r.get("id"): r.get("played_online", False) for r in old_rows}
    for r in new_rows:
        if r.get("id") in online:
            r["played_online"] = online[r["id"]]


def _scan_stable_incremental(job, paths: dict, fresh: bool) -> None:
    import glob as _glob
    from .stable_scanner import load_db_maps, load_score_map, parse_stable_file
    base = paths["songs_dir"]
    fp_new = cachemod.fingerprint_stable(base, paths["osu_db"], paths["scores_db"])
    old_keys, old_rows, old_fp = cachemod.load_scan("stable") if not fresh else ([], [], {})
    db, scores = load_db_maps(paths["osu_db"]), load_score_map(paths["scores_db"])

    def _parse_all(files: list[str]) -> tuple[list, list]:
        keys, rows = [], []
        job.total = len(files)
        job.done = 0
        for full in files:
            rel = os.path.relpath(full, base)
            keys.append(rel)
            rows.append(parse_stable_file(full, db, scores).__dict__)
            job.done += 1
        return keys, rows

    if fresh or not old_rows or not old_fp:
        files = sorted(_glob.glob(os.path.join(_glob.escape(base or ""), "*", "*.osu"))) if base else []
        keys, rows = _parse_all(files)
    else:
        unchanged, changed, _deleted, dbs_changed = cachemod.diff_fingerprints(old_fp, fp_new)
        # files needing (re-)parse; when the dbs changed every row must rejoin
        to_parse = sorted(set(changed) | (set(unchanged) if dbs_changed else set()))
        job.total = len(to_parse)
        job.done = 0
        fresh_rows: dict[str, dict] = {}
        for rel in to_parse:
            full = os.path.join(base, rel)
            if os.path.exists(full):
                fresh_rows[rel] = parse_stable_file(full, db, scores).__dict__
            job.done += 1
        old_by_key = {k: r for k, r in zip(old_keys, old_rows) if k is not None}
        keys = sorted(set(fp_new["files"]))
        rows = [fresh_rows.get(rel, old_by_key.get(rel)) for rel in keys]
        rows = [r for r in rows if r is not None]
        keys = [k for k, r in zip(keys, [fresh_rows.get(rel, old_by_key.get(rel)) for rel in keys]) if r is not None]
        _carry_online(old_rows, rows)
    for b in _db_only_rows(paths):
        keys.append(None)
        rows.append(b.__dict__)
    _carry_online(old_rows, rows)
    cachemod.save_scan("stable", keys, rows, fp_new)
    job.done = job.total


def _db_only_rows(paths: dict) -> list:
    """Entries from osu!.db with no file on disk (cheap: db read only)."""
    from .stable_scanner import _played_from_db  # noqa
    from .stable_db import GRADE_NAMES, RANKED_NAMES, read_osu_db
    from .library import Beatmap
    from .osu_parser import mode_name
    try:
        _, dbmaps = read_osu_db(paths["osu_db"])
    except (OSError, ValueError):
        return []
    import glob as _glob
    on_disk = set()
    if paths["songs_dir"] and os.path.isdir(paths["songs_dir"]):
        for p in _glob.glob(os.path.join(_glob.escape(paths["songs_dir"]), "*", "*.osu")):
            on_disk.add(os.path.basename(p))
    from .stable_scanner import load_score_map
    score_map = load_score_map(paths["scores_db"])
    out = []
    for m in dbmaps:
        if m.osu_file in on_disk:
            continue
        scores = score_map.get(m.md5.lower(), [])
        g = m.grades[m.mode] if 0 <= m.mode < 4 else 0
        out.append(Beatmap(
            id=m.md5, set_id=f"missing:{m.folder}",
            artist=m.artist, title=m.title, creator=m.creator, diff=m.difficulty,
            source=m.source, tags=m.tags, mode=m.mode, mode_name=mode_name(m.mode),
            ar=m.ar, cs=m.cs, od=m.od, hp=m.hp, stars=m.stars, length_ms=m.total_ms,
            beatmap_id=m.beatmap_id, ranked=RANKED_NAMES.get(m.ranked, "unknown"),
            played_local=_played_from_db(any(x for x in m.grades), m.unplayed,
                                         m.last_played, len(scores)),
            grade=GRADE_NAMES.get(g, ""), last_played=m.last_played,
            score_count=len(scores), folder=m.folder + " (missing .osu)", origin="stable"))
    return out


def _scan_lazer_incremental(job, paths: dict, fresh: bool) -> None:
    from .lazer_scanner import load_realm_export, parse_lazer_blob, scan_lazer
    fp_new = cachemod.fingerprint_lazer(paths["lazer_dir"])
    old_keys, old_rows, old_fp = cachemod.load_scan("lazer") if not fresh else ([], [], {})
    realm = {"played_ids": set(), "played_md5": set(), "stars": {}}
    if paths.get("realm_export"):
        try:
            realm = load_realm_export(paths["realm_export"])
        except (OSError, ValueError):
            pass
    if fresh or not old_rows or not old_fp:
        maps = scan_lazer(paths["lazer_dir"], paths.get("realm_export", ""))
        job.total, job.done = len(maps), len(maps)
        keys = _lazer_keys(paths["lazer_dir"], maps)
        cachemod.save_scan("lazer", keys, to_dict_list(maps), fp_new)
        return
    unchanged, changed, _deleted, _dbs = cachemod.diff_fingerprints(old_fp, fp_new)
    old_by_key = {k: r for k, r in zip(old_keys, old_rows) if k is not None}
    job.total = len(fp_new["files"])
    job.done = len(unchanged)
    base = os.path.join(paths["lazer_dir"], "files")
    new_keys = sorted(set(fp_new["files"]))
    new_rows = []
    for rel in new_keys:
        if rel in old_by_key and rel not in changed:
            new_rows.append(old_by_key[rel])
            continue
        full = os.path.join(base, rel)
        try:
            new_rows.append(parse_lazer_blob(full, realm).__dict__)
        except OSError:
            pass
        job.done += 1
    _carry_online(old_rows, new_rows)
    cachemod.save_scan("lazer", new_keys, new_rows, fp_new)


def _lazer_keys(lazer_dir: str, maps) -> list:
    """Best-effort blob keys: positional match when counts agree, else None."""
    files_dir = os.path.join(lazer_dir, "files")
    found: list[str] = []
    if files_dir and os.path.isdir(files_dir):
        for root, _d, names in os.walk(files_dir):
            for n in names:
                found.append(os.path.join(root, n))
        found.sort()
    if len(found) == len(maps):
        return [os.path.relpath(p, files_dir) for p in found]
    return [None] * len(maps)


def run_scan(mode: str, fresh: bool) -> str:
    settings = load_settings()
    paths = library_paths(settings, mode)
    fp = cachemod.fingerprint(mode, **paths)
    total = len(fp.get("files", {})) or 1
    job = registry.create("scan", total=total)
    if mode == "lazer":
        registry.run_background(job, lambda j: _scan_lazer_incremental(j, paths, fresh))
    else:
        registry.run_background(job, lambda j: _scan_stable_incremental(j, paths, fresh))
    return job.id


def run_online_check() -> tuple[str, str]:
    """Returns (job_id, error)."""
    settings = load_settings()
    token = valid_token(settings)
    if not token:
        return "", "account not linked — POST /api/auth/code first"
    user_id = _load_token().get("user_id") or settings.api.user_id
    if not user_id:
        return "", "user id unknown — link account again"
    keys, rows, fp = cachemod.load_scan(get_mode())
    if not rows:
        return "", "library empty — run a scan first"

    def _fn(job):
        from .osu_api import mark_online_played
        maps = from_dict_list(rows)
        checkable = sum(1 for b in maps if b.beatmap_id not in (None, -1, 0))
        job.total = checkable
        job.done = 0

        def _prog(d, t):
            job.done = d
            job.total = t

        mark_online_played(maps, int(user_id), token, progress=_prog)
        cachemod.save_scan(get_mode(), keys, to_dict_list(maps), fp)

    job = registry.create("online", total=1)
    registry.run_background(job, _fn)
    return job.id, ""


# ---- HTTP ----

class Handler(BaseHTTPRequestHandler):
    server_version = "OsuLibrarian/0.2"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    def _json(self, code: int, obj: dict, headers: dict | None = None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            return self._status()
        if path == "/api/library":
            return self._library()
        if path.startswith("/api/jobs/"):
            job = registry.get(path.rsplit("/", 1)[-1])
            if job is None:
                return self._json(404, {"error": "unknown job"})
            return self._json(200, job.to_dict())
        if path == "/api/auth/url":
            return self._auth_url()
        if path == "/api/auth/status":
            tok = _load_token()
            return self._json(200, {"linked": bool(tok.get("access_token")),
                                   "user_id": tok.get("user_id", 0)})
        return self._static(path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json()
        if path == "/api/scan":
            mode = body.get("mode") or get_mode()
            if mode not in ("stable", "lazer"):
                return self._json(400, {"error": "mode must be stable|lazer"})
            set_mode(mode)
            return self._json(200, {"job_id": run_scan(mode, bool(body.get("fresh")))})
        if path == "/api/mode":
            mode = body.get("mode", "")
            if mode not in ("stable", "lazer"):
                return self._json(400, {"error": "mode must be stable|lazer"})
            set_mode(mode)
            return self._json(200, {"mode": mode})
        if path == "/api/auth/code":
            return self._auth_code(body.get("code", ""))
        if path == "/api/online-check":
            jid, err = run_online_check()
            if err:
                return self._json(400, {"error": err})
            return self._json(200, {"job_id": jid})
        if path == "/api/export":
            return self._export(body)
        return self._json(404, {"error": "not found"})

    # -- endpoints --

    def _status(self):
        s = load_settings()
        mode = get_mode()
        keys, rows, fp = cachemod.load_scan(mode)
        paths = library_paths(s, mode)
        if mode == "lazer":
            lok = os.path.isdir(os.path.join(paths["lazer_dir"], "files"))
            ok = {"songs_ok": lok, "db_ok": lok, "lazer_ok": lok}
        else:
            ok = {"songs_ok": os.path.isdir(paths["songs_dir"]),
                  "db_ok": os.path.isfile(paths["osu_db"]) if paths["osu_db"] else False,
                  "lazer_ok": False}
        counts = summarize(from_dict_list(rows)) if rows else {"diffs": 0, "sets": 0,
                                                               "played": 0, "unplayed": 0}
        tok = _load_token()
        self._json(200, {
            "mode": mode, **ok, "counts": counts,
            "scan": {"version": _version(rows, fp) if fp else "none",
                     "cached": bool(rows)},
            "jobs": {"active": registry.active()},
            "auth": {"linked": bool(tok.get("access_token")),
                     "user_id": tok.get("user_id", 0)},
        })

    def _library(self):
        _keys, rows, fp = cachemod.load_scan(get_mode())
        version = _version(rows, fp) if fp else "empty"
        if self.headers.get("If-None-Match") == f'"{version}"':
            self.send_response(304)
            self.end_headers()
            return
        body = json.dumps({"version": version, "maps": rows}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", f'"{version}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_url(self):
        from .osu_api import authorize_url
        s = load_settings()
        if not s.api.client_id:
            return self._json(400, {"error": "client_id not configured (settings.json → api)"})
        return self._json(200, {"url": authorize_url(s.api.client_id, s.api.redirect_uri)})

    def _auth_code(self, code: str):
        from .osu_api import exchange_code, get_me
        if not code:
            return self._json(400, {"error": "missing code"})
        s = load_settings()
        try:
            tok = exchange_code(s.api.client_id, s.api.client_secret,
                                s.api.redirect_uri, code)
        except Exception as e:
            return self._json(400, {"error": f"exchange failed: {e}"})
        tok["obtained_at"] = time.time()
        me = get_me(tok.get("access_token", ""))
        if me.get("id"):
            tok["user_id"] = me["id"]
            tok["username"] = me.get("username", "")
        _save_token(tok)
        return self._json(200, {"ok": True, "user_id": tok.get("user_id", 0),
                               "username": tok.get("username", "")})

    def _export(self, body: dict):
        from . import export as exportmod
        fmt = body.get("format", "json")
        if fmt not in exportmod.EXPORTERS:
            return self._json(400, {"error": "format must be json|txt|collection"})
        s = load_settings()
        _keys, rows, _fp = cachemod.load_scan(get_mode())
        want = set(body.get("ids", []))
        sel = [r for r in rows if r.get("id") in want] if want else rows
        if fmt == "collection":
            payload = exportmod.to_collection_db(sel, body.get("name", "Librarian Export"))
        else:
            payload = exportmod.EXPORTERS[fmt](sel)
        digest = hashlib.sha1(payload).hexdigest()[:12]
        self.send_response(200)
        self.send_header("Content-Type", exportmod.MIMES[fmt])
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{exportmod.FILENAMES[fmt]}"')
        self.send_header("X-Export-Count", str(len(sel)))
        self.send_header("X-Export-Sha", digest)
        self.end_headers()
        self.wfile.write(payload)

    def _static(self, path: str):
        if path == "/":
            path = "/index.html"
        rel = urllib.parse.unquote(path.lstrip("/"))
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(full):
            return self._json(404, {"error": "not found"})
        ctype, _ = mimetypes.guess_type(full)
        try:
            with open(full, "rb") as f:
                body = f.read()
            mtime = os.stat(full).st_mtime
        except OSError:
            return self._json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", time.strftime(
            "%a, %d %b %Y %H:%M:%S GMT", time.gmtime(mtime)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8787) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"osu! librarian web at http://127.0.0.1:{port} (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
