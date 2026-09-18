"""Local web server: JSON API + static frontend. Stdlib only.

Endpoints (see docs/webui-spec.md for the contract):
  GET  /                        web/index.html (+ static assets)
  GET  /api/status
  GET  /api/library             (ETag / If-None-Match -> 304)
  POST /api/scan                {mode?, fresh?} -> {job_id}
  GET  /api/jobs/:id
  GET  /api/auth/url
  POST /api/auth/code           {code} -> {ok, user_id}
   GET  /api/auth/callback       ?code=... (&error=...) -> HTML (OAuth redirect, auto-links)
   POST /api/auth/config         {client_id, client_secret} -> {ok:true}
   GET  /api/auth/status         {linked, user_id, username, redirect_uri, configured,
                                 relay: {url, client_id, available}}
   GET  /api/auth/relay-return   ?ticket=.. -> HTML (one-click relay return, saves token)
  POST /api/online-check        {} -> {job_id}
  POST /api/export              {ids, format} -> file download
"""
from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cache as cachemod
from .config import Settings, load_settings
from .jobs import JobRegistry
from .library import from_dict_list, summarize, to_dict_list

TOKEN_PATH = ".token.json"
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
MAX_BODY_BYTES = 2 * 1024 * 1024

registry = JobRegistry()
_settings_lock = threading.Lock()
_scan_lock = threading.Lock()
_current_mode: str | None = None  # active mode for this server process

_installs_cache: dict = {"at": 0.0, "items": []}
_INSTALLS_TTL_S = 60.0


def _cached_installs() -> list:
    """Install detection re-globs wine prefixes; cache it briefly for polling."""
    now = time.time()
    try:
        fresh = now - float(_installs_cache.get("at", 0.0)) < _INSTALLS_TTL_S
    except (TypeError, ValueError):
        fresh = False
    if fresh and isinstance(_installs_cache.get("items"), list):
        return _installs_cache["items"]
    from .detect import find_installs
    items = [i.to_dict() for i in find_installs()]
    _installs_cache["at"] = now
    _installs_cache["items"] = items
    return items


def _summarize_rows(rows: list) -> dict:
    """Counts straight from raw scan dicts (no Beatmap objects to build)."""
    diffs = len(rows)
    sets = len({r.get("set_id") for r in rows if isinstance(r, dict)})
    played = sum(1 for r in rows if isinstance(r, dict)
                 and (r.get("played_local") or r.get("played_online")))
    return {"diffs": diffs, "sets": sets, "played": played,
            "unplayed": diffs - played}


_art_index: dict = {"key": None, "by_id": {}}


def _art_row_index(mode: str, rows: list, fp: dict) -> dict:
    """Row id -> row, rebuilt only when the scan changes.

    Keyed by scan-file mtime + version: the version alone doesn't cover row
    content (counts + fingerprint only), so two different scans could share
    it and a version-only key would serve stale rows.
    """
    try:
        mtime = os.stat(cachemod.cache_paths(mode)[0]).st_mtime_ns
    except OSError:
        mtime = 0
    try:
        ver = _version(rows, fp) if fp else f"empty:{len(rows)}"
    except Exception:
        ver = len(rows or [])
    key = (mode, mtime, ver, len(rows or []))
    if _art_index.get("key") != key:
        by_id = {}
        for r in rows or []:
            if isinstance(r, dict) and r.get("id") and r["id"] not in by_id:
                by_id[r["id"]] = r
        _art_index["key"] = key
        _art_index["by_id"] = by_id
    return _art_index["by_id"]


class _BodyTooLarge(Exception):
    pass


def _atomic_write_json(path: str, obj, indent=None) -> None:
    d = os.path.dirname(path) or "."
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_scan_atomic(mode: str, keys: list, rows: list[dict], fp: dict) -> None:
    scan_path, manifest_path = cachemod.cache_paths(mode)
    _atomic_write_json(scan_path, {"keys": keys, "rows": rows})
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifests = json.load(f)
            if not isinstance(manifests, dict):
                manifests = {}
    except (OSError, ValueError):
        manifests = {}
    manifests[mode] = fp
    _atomic_write_json(manifest_path, manifests, indent=1)


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


def _clean_rows(rows) -> list[dict]:
    """Drop corrupt cache rows so one bad row can't break status/library/export."""
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


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


def _token_linked(tok: dict, settings: Settings | None = None) -> bool:
    """Cheap linked probe for status payloads: no network, no refresh.

    Fresh tokens read linked; expired ones still read linked while a refresh
    could succeed (refresh_token + app credentials configured), because only
    a refresh attempt proves them dead. So status.linked can read true while
    the next online-check job fails with a dead token — that split is
    deliberate, to keep the polled endpoints free of blocking network calls.
    The job error then tells the user to link the account again via the app
    dialog.
    """
    if not isinstance(tok, dict) or not tok.get("access_token"):
        return False
    try:
        obtained = float(tok.get("obtained_at", 0))
    except (TypeError, ValueError):
        obtained = 0.0
    try:
        expires = int(tok.get("expires_in", 0))
    except (TypeError, ValueError):
        try:
            expires = int(float(tok.get("expires_in", 0)))
        except (TypeError, ValueError):
            expires = 0
    if expires and time.time() - obtained < expires - 60:
        return True
    if settings is not None and tok.get("refresh_token") \
            and settings.api.client_id and settings.api.client_secret:
        return True
    if not expires or not obtained:
        return True  # legacy token file without timestamps: presence only
    return False


def valid_token(settings: Settings) -> str:
    """Usable access token or '' (refreshes server-side when possible)."""
    tok = _load_token()
    if not tok.get("access_token"):
        return ""
    try:
        _obtained = float(tok.get("obtained_at", 0))
    except (TypeError, ValueError):
        _obtained = 0.0
    try:
        _expires = int(tok.get("expires_in", 0))
    except (TypeError, ValueError):
        try:
            _expires = int(float(tok.get("expires_in", 0)))
        except (TypeError, ValueError):
            _expires = 0
    age = time.time() - _obtained
    if age < _expires - 60:
        return tok["access_token"]
    if tok.get("refresh_token") and settings.api.client_id and settings.api.client_secret:
        from .osu_api import refresh_token as _rt
        try:
            new = _rt(settings.api.client_id, settings.api.client_secret,
                      tok["refresh_token"])
            new["obtained_at"] = time.time()
            if "refresh_token" not in new:
                new["refresh_token"] = tok["refresh_token"]
            for _k in ("user_id", "username"):
                if _k not in new and _k in tok:
                    new[_k] = tok[_k]
            try:
                from .osu_api import get_me as _get_me
                _me = _get_me(new.get("access_token", ""))
                if isinstance(_me, dict) and _me.get("id"):
                    try:
                        new["user_id"] = int(_me["id"])
                    except (TypeError, ValueError):
                        new["user_id"] = _me["id"]
                    new["username"] = _me.get("username", "")
            except Exception:
                pass
            _save_token(new)
            return new.get("access_token", "")
        except Exception:
            return ""
    return ""


def _persist_token(tok: dict) -> tuple[bool, dict | str]:
    """Stamp obtained_at, attach user identity via get_me, save (0600).

    Returns (ok, payload_or_error); error strings never include secrets.
    Shared by the manual code exchange and the relay one-click return.
    """
    from .osu_api import get_me
    if not isinstance(tok, dict) or not tok.get("access_token"):
        return False, "link failed: bad token response"
    tok["obtained_at"] = time.time()
    try:
        me = get_me(tok.get("access_token", ""))
    except Exception:
        me = {}
    if not (isinstance(me, dict) and me.get("id")):
        try:
            me = get_me(tok.get("access_token", ""))
        except Exception:
            me = {}
    if isinstance(me, dict) and me.get("id"):
        try:
            tok["user_id"] = int(me["id"])
        except (TypeError, ValueError):
            tok["user_id"] = me["id"]
        tok["username"] = me.get("username", "")
        _save_token(tok)
        return True, {"user_id": tok.get("user_id", 0),
                      "username": tok.get("username", "")}
    try:
        print("warning: linked without identity (get_me failed)", file=sys.stderr)
    except Exception:
        pass
    _save_token(tok)
    return True, {"user_id": tok.get("user_id", 0),
                  "username": tok.get("username", ""),
                  "linked_without_identity": True}


def _finish_auth(code: str) -> tuple[bool, dict | str]:
    """Exchange an OAuth code, persist the token, return (ok, payload_or_error).

    On success payload is {"user_id": int, "username": str}; on failure the
    second element is a human-readable error string (never includes secrets).
    Shared by POST /api/auth/code (CLI compat) and GET /api/auth/callback.
    """
    from .osu_api import exchange_code
    if not code:
        return False, "missing code"
    s = load_settings()
    try:
        tok = exchange_code(s.api.client_id, s.api.client_secret,
                            s.api.redirect_uri, code)
    except Exception as e:
        return False, f"exchange failed: {e}"
    if not isinstance(tok, dict):
        return False, "exchange failed: bad response"
    return _persist_token(tok)


# Relay one-click linking (frontend-built authorize URL, no new endpoint):
#   https://osu.ppy.sh/oauth/authorize?client_id=<RELAY_CLIENT_ID>
#     &redirect_uri=<RELAY_URL>/auth/callback&response_type=code
#     &scope=identify+public&state=<ticket>.<localport>
# osu! redirects to the relay (GET /auth/callback); the relay then redirects
# the browser to http://127.0.0.1:<localport>/api/auth/relay-return?ticket=
# <ticket>, which fetches {relay_url}/token?ticket=.. server-side and saves it.
_TICKET_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _relay_settings() -> tuple[str, int]:
    s = load_settings()
    url = (s.api.relay_url or "").strip()
    try:
        cid = int(s.api.relay_client_id or 0)
    except (TypeError, ValueError):
        cid = 0
    return url, cid


def _fetch_relay_token(relay_url: str, ticket: str) -> tuple[bool, dict | str]:
    """One-time fetch of the relay-held token. Never includes secrets in errors.

    Token fetch contract: POST /token with JSON {"ticket": ...} first (same
    response/semantics as GET); fall back to GET on 404/405/connection error
    so relays that only speak GET keep working.
    """
    base = relay_url.rstrip("/")

    def _drain(e) -> None:
        try:
            e.read()
        except Exception:
            pass
        finally:
            try:
                e.close()
            except Exception:
                pass

    try:
        data = json.dumps({"ticket": ticket}).encode()
        req = urllib.request.Request(
            f"{base}/token", data=data,
            headers={"Accept": "application/json",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            try:
                body = json.loads(r.read().decode("utf-8") or "null")
            except ValueError:
                return False, "link failed: bad relay response — check your connection and retry"
        if not isinstance(body, dict) or not body.get("access_token"):
            return False, "link failed: link expired or already used — click the link again to get a fresh link"
        return True, body
    except urllib.error.HTTPError as e:
        if getattr(e, "code", None) not in (404, 405):
            _drain(e)
            return False, "link failed: link expired or already used — click the link again to get a fresh link"
        _drain(e)  # no POST route here: fall through to the GET fallback
    except Exception:
        pass  # connection error: fall through to the GET fallback
    url = f"{base}/token?ticket={urllib.parse.quote(ticket, safe='')}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            try:
                body = json.loads(r.read().decode("utf-8") or "null")
            except ValueError:
                return False, "link failed: bad relay response — check your connection and retry"
    except urllib.error.HTTPError as e:
        _drain(e)
        return False, "link failed: link expired or already used — click the link again to get a fresh link"
    except Exception:
        return False, "link failed: linking service unreachable — check your connection and retry"
    if not isinstance(body, dict) or not body.get("access_token"):
        return False, "link failed: link expired or already used — click the link again to get a fresh link"
    return True, body


# ---- scans ----

def _carry_online(old_rows: list[dict], new_rows: list[dict]) -> None:
    """Keep played_online flags across re-parses (online state isn't on disk).

    Joins on the md5 id first; when a map was edited (new md5) fall back to
    the stable beatmap_id so replayed/edited maps don't lose their flag.
    """
    online = {r.get("id"): r.get("played_online", False) for r in old_rows}
    by_bid: dict = {}
    for r in old_rows:
        bid = r.get("beatmap_id")
        if bid not in (None, -1, 0) and bid not in by_bid:
            by_bid[bid] = r.get("played_online", False)
    for r in new_rows:
        if r.get("id") in online:
            r["played_online"] = online[r["id"]]
            continue
        bid = r.get("beatmap_id")
        if bid not in (None, -1, 0) and bid in by_bid:
            r["played_online"] = by_bid[bid]


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
    for b in _db_only_rows(paths, list(db.values()), scores):
        keys.append(None)
        rows.append(b.__dict__)
    _carry_online(old_rows, rows)
    _save_scan_atomic("stable", keys, rows, fp_new)
    job.done = job.total


def _db_only_rows(paths: dict, dbmaps=None, score_map=None) -> list:
    """Entries from osu!.db with no file on disk (cheap: db read only).

    dbmaps/score_map are the already-loaded scan inputs: passing them avoids
    re-reading osu!.db + scores.db a second time per stable scan. When omitted
    (None) they are read from disk as before.
    """
    from .stable_scanner import _played_from_db  # noqa
    from .stable_db import GRADE_NAMES, RANKED_NAMES, read_osu_db
    from .library import Beatmap
    from .osu_parser import mode_name
    if dbmaps is None:
        try:
            _, dbmaps = read_osu_db(paths["osu_db"])
        except (OSError, ValueError):
            return []
    if score_map is None:
        from .stable_scanner import load_score_map
        score_map = load_score_map(paths["scores_db"])
    import glob as _glob
    on_disk = set()
    if paths["songs_dir"] and os.path.isdir(paths["songs_dir"]):
        for p in _glob.glob(os.path.join(_glob.escape(paths["songs_dir"]), "*", "*.osu")):
            on_disk.add(os.path.basename(p))
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


def _realm_fingerprint(realm_export: str, lazer_dir: str):
    """JSON-serializable realm state for the lazer fingerprint (mtime + hash)."""
    if realm_export:
        try:
            st = os.stat(realm_export)
        except OSError:
            return []
        try:
            h = hashlib.sha1()
            with open(realm_export, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return [st.st_mtime_ns, st.st_size, h.hexdigest()[:16]]
        except OSError:
            try:
                return [st.st_mtime_ns, st.st_size]
            except OSError:
                return []
    try:
        if lazer_dir:
            st = os.stat(os.path.join(lazer_dir, "client.realm"))
            return [st.st_mtime_ns, st.st_size]
    except OSError:
        pass
    return []


def _scan_lazer_incremental(job, paths: dict, fresh: bool) -> None:
    from .lazer_scanner import (_is_osu_blob, load_realm_export, parse_lazer_blob)
    lazer_dir = paths.get("lazer_dir", "") or ""
    files_dir = os.path.join(lazer_dir, "files")
    fp_new = cachemod.fingerprint_lazer(lazer_dir)
    old_keys, old_rows, old_fp = cachemod.load_scan("lazer") if not fresh else ([], [], {})
    realm = {"played_ids": set(), "played_md5": set(), "stars": {},
             "grades": {}, "statuses": {}, "dates": {}, "set_files": {}}
    effective_export = paths.get("realm_export", "") or ""
    if effective_export:
        try:
            realm = load_realm_export(effective_export)
        except (OSError, ValueError):
            pass
    else:
        # No explicit export configured: try the node helper (cached dump).
        try:
            from .lazer_scanner import normalize_realm_dump
            from .realm_export import export_realm as _export_realm
            dump = _export_realm(lazer_dir)
            if isinstance(dump, dict):
                realm = normalize_realm_dump(dump)
        except Exception:
            pass
    fp_new["realm"] = _realm_fingerprint(effective_export, lazer_dir)

    def _blobs() -> list[str]:
        out = []
        if files_dir and os.path.isdir(files_dir):
            for root, _d, names in os.walk(files_dir):
                for n in names:
                    p = os.path.join(root, n)
                    if _is_osu_blob(p):
                        out.append(p)
        return sorted(out)

    if fresh or not old_rows or not old_fp:
        blobs = _blobs()
        job.total, job.done = len(blobs) or 1, 0
        keys, rows = [], []
        for full in blobs:
            rel = os.path.relpath(full, files_dir)
            keys.append(rel)
            rows.append(parse_lazer_blob(full, realm).__dict__)
            job.done += 1
        _carry_online(old_rows, rows)
    else:
        _unchanged, changed, _deleted, _dbs = cachemod.diff_fingerprints(old_fp, fp_new)
        realm_changed = old_fp.get("realm") != fp_new.get("realm")
        old_by_key = {k: r for k, r in zip(old_keys, old_rows) if k is not None}
        new_keys = sorted(set(fp_new["files"]))
        job.total, job.done = len(new_keys) or 1, 0
        keys, rows = [], []
        for rel in new_keys:
            old = old_by_key.get(rel)
            if old is not None and rel not in changed and not realm_changed:
                keys.append(rel)
                rows.append(old)
            else:
                full = os.path.join(files_dir, rel)
                # Non-.osu blobs (audio/images) get no row; deleted files are
                # dropped. done still ticks so progress never exceeds total.
                # When only the realm changed we still re-parse to rejoin
                # fresh played/grade/stars onto the reused rows.
                if os.path.isfile(full) and _is_osu_blob(full):
                    keys.append(rel)
                    rows.append(parse_lazer_blob(full, realm).__dict__)
            job.done += 1
        _carry_online(old_rows, rows)
    _save_scan_atomic("lazer", keys, rows, fp_new)
    job.done = job.total


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
    """Start a scan job; '' when another scan/online-check is already running."""
    if not _scan_lock.acquire(blocking=False):
        return ""
    try:
        settings = load_settings()
        paths = library_paths(settings, mode)
        fp = cachemod.fingerprint(mode, **paths)
        total = len(fp.get("files", {})) or 1
        job = registry.create("scan", total=total)
    except BaseException:
        try:
            _scan_lock.release()
        except RuntimeError:
            pass
        raise
    if mode == "lazer":
        _target = lambda j: _scan_lazer_incremental(j, paths, fresh)
    else:
        _target = lambda j: _scan_stable_incremental(j, paths, fresh)

    def _wrapped(j):
        try:
            _target(j)
        finally:
            try:
                _scan_lock.release()
            except RuntimeError:
                pass

    registry.run_background(job, _wrapped)
    return job.id


def run_online_check() -> tuple[str, str]:
    """Returns (job_id, error).

    Only the scan-lock probe runs on the request thread so POST returns
    immediately. Token refresh (up to ~30s of network) plus all validation
    runs inside the worker; a dead/missing token fails the JOB with a clear
    message instead of blocking the response.
    """
    if not _scan_lock.acquire(blocking=False):
        return "", "scan already in progress"

    def _fn(job):
        try:
            from .osu_api import mark_online_played
            # Blocking refresh lives here (worker thread), never on requests.
            settings = load_settings()
            token = valid_token(settings)
            if not token:
                raise RuntimeError("account not linked or session expired — "
                                   "link the account again via the app dialog")
            user_id = _load_token().get("user_id") or settings.api.user_id
            if not user_id:
                raise RuntimeError("user id unknown — "
                                   "link the account again via the app dialog")
            mode = get_mode()
            keys, rows, fp = cachemod.load_scan(mode)
            if not rows:
                raise RuntimeError("library empty — run a scan first")
            maps = from_dict_list(rows)
            checkable = sum(1 for b in maps if b.beatmap_id not in (None, -1, 0))
            job.total = checkable
            job.done = 0

            def _prog(d, t):
                job.done = d
                job.total = t

            stats = mark_online_played(maps, int(user_id), token, progress=_prog)
            try:
                job.meta = stats
            except Exception:
                pass
            _save_scan_atomic(mode, keys, to_dict_list(maps), fp)
        finally:
            try:
                _scan_lock.release()
            except RuntimeError:
                pass

    job = registry.create("online", total=1)
    try:
        job.meta = {}
    except Exception:
        pass
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

    def _send_html(self, code: int, body: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return {}
        if n > MAX_BODY_BYTES:
            self.close_connection = True
            try:
                self.rfile.read(min(n, 65536))
            except Exception:
                pass
            raise _BodyTooLarge()
        try:
            remaining = n
            chunks = []
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > MAX_BODY_BYTES:
                    self.close_connection = True
                    raise _BodyTooLarge()
                remaining -= len(chunk)
            parsed = json.loads(b"".join(chunks).decode("utf-8") or "{}")
        except _BodyTooLarge:
            raise
        except ValueError:
            return {}
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            return self._status()
        if path == "/api/library":
            return self._library()
        if path == "/api/art":
            return self._art(parsed.query)
        if path.startswith("/api/jobs/"):
            job = registry.get(path.rsplit("/", 1)[-1])
            if job is None:
                return self._json(404, {"error": "unknown job"})
            payload = job.to_dict()
            # Workers stash extra stats (e.g. online-check counts) on job.meta;
            # surface them here so 401/429 storms are visible while polling.
            try:
                meta = getattr(job, "meta", None)
            except Exception:
                meta = None
            if isinstance(meta, dict):
                payload["meta"] = meta
            return self._json(200, payload)
        if path == "/api/auth/url":
            return self._auth_url()
        if path == "/api/auth/callback":
            return self._auth_callback(parsed.query)
        if path == "/api/auth/status":
            tok = _load_token()
            s = load_settings()
            relay_url = (s.api.relay_url or "").strip()
            try:
                relay_cid = int(s.api.relay_client_id or 0)
            except (TypeError, ValueError):
                relay_cid = 0
            return self._json(200, {"linked": _token_linked(tok, s),
                                   "user_id": tok.get("user_id", 0),
                                   "username": tok.get("username", ""),
                                   "redirect_uri": s.api.redirect_uri,
                                   "configured": bool(s.api.client_id and s.api.client_secret),
                                   "relay": {"url": relay_url,
                                             "client_id": relay_cid,
                                             "available": bool(relay_url and relay_cid)}})
        if path == "/api/auth/relay-return":
            return self._auth_relay_return(parsed.query)
        if path == "/api/detect":
            from .detect import find_installs
            return self._json(200, {"installs": [i.to_dict() for i in find_installs()]})
        return self._static(path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json()
        except _BodyTooLarge:
            return self._json(413, {"error": "request body too large"})
        if not isinstance(body, dict):
            body = {}
        if path == "/api/scan":
            mode = body.get("mode") or get_mode()
            if mode not in ("stable", "lazer"):
                return self._json(400, {"error": "mode must be stable|lazer"})
            set_mode(mode)
            jid = run_scan(mode, bool(body.get("fresh")))
            if not jid:
                return self._json(409, {"error": "scan already in progress"})
            return self._json(200, {"job_id": jid})
        if path == "/api/mode":
            mode = body.get("mode", "")
            if mode not in ("stable", "lazer"):
                return self._json(400, {"error": "mode must be stable|lazer"})
            set_mode(mode)
            return self._json(200, {"mode": mode})
        if path == "/api/auth/code":
            return self._auth_code(body.get("code", ""))
        if path == "/api/auth/config":
            return self._auth_config(body)
        if path == "/api/online-check":
            jid, err = run_online_check()
            if err:
                if err == "scan already in progress":
                    return self._json(409, {"error": err})
                return self._json(400, {"error": err})
            return self._json(200, {"job_id": jid})
        if path == "/api/export":
            return self._export(body)
        if path == "/api/use-install":
            return self._use_install(body)
        return self._json(404, {"error": "not found"})

    # -- endpoints --

    def _status(self):
        s = load_settings()
        mode = get_mode()
        keys, rows, fp = cachemod.load_scan(mode)
        rows = _clean_rows(rows)
        paths = library_paths(s, mode)
        if mode == "lazer":
            lok = os.path.isdir(os.path.join(paths["lazer_dir"], "files"))
            ok = {"songs_ok": lok, "db_ok": lok, "lazer_ok": lok}
        else:
            ok = {"songs_ok": os.path.isdir(paths["songs_dir"]),
                  "db_ok": os.path.isfile(paths["osu_db"]) if paths["osu_db"] else False,
                  "lazer_ok": False}
        counts = _summarize_rows(rows) if rows else {"diffs": 0, "sets": 0,
                                                              "played": 0, "unplayed": 0}
        tok = _load_token()
        self._json(200, {
            "mode": mode, **ok, "counts": counts,
            "scan": {"version": _version(rows, fp) if fp else "none",
                     "cached": bool(rows)},
            "jobs": {"active": registry.active()},
            "auth": {"linked": _token_linked(tok, s),
                     "user_id": tok.get("user_id", 0),
                     "username": tok.get("username", "")},
            "installs": _cached_installs(),
        })

    def _library(self):
        # NOTE: the whole library ships unpaged here (can be multi-MB); full
        # paging is out of scope. Private revalidation + ETag keeps polling
        # cheap via conditional 304s.
        _keys, rows, fp = cachemod.load_scan(get_mode())
        rows = _clean_rows(rows)
        version = _version(rows, fp) if fp else "empty"
        etag = f'"{version}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            self.end_headers()
            return
        body = json.dumps({"version": version, "maps": rows}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _use_install(self, body: dict):
        """Adopt a detected install: persist paths to settings.json + switch mode."""
        from .config import save_settings
        kind, path = body.get("kind", ""), body.get("path", "")
        if kind not in ("stable", "lazer") or not path or not os.path.isdir(path):
            return self._json(400, {"error": "unknown install — pick one from /api/detect"})
        s = load_settings()
        if kind == "stable":
            s.stable_dir, s.songs_dir, s.osu_db, s.scores_db = path, "", "", ""
        else:
            s.lazer_dir = path
        s = s.resolved()
        save_settings(s)
        set_mode(kind)
        return self._json(200, {"ok": True, "mode": kind,
                               "paths": library_paths(s, kind)})

    def _auth_url(self):
        from .osu_api import authorize_url
        s = load_settings()
        if not s.api.client_id:
            return self._json(400, {"error": "client_id not configured (settings.json → api)"})
        return self._json(200, {"url": authorize_url(s.api.client_id, s.api.redirect_uri)})

    def _auth_code(self, code: str):
        ok, result = _finish_auth(code if isinstance(code, str) else "")
        if not ok:
            return self._json(400, {"error": result})
        assert isinstance(result, dict)
        return self._json(200, {"ok": True, "user_id": result.get("user_id", 0),
                               "username": result.get("username", "")})

    def _auth_config(self, body: dict):
        from .config import save_settings
        cid_raw = body.get("client_id", 0)
        secret = body.get("client_secret", "")
        try:
            cid = int(cid_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return self._json(400, {"error": "client_id must be a positive int"})
        if cid <= 0:
            return self._json(400, {"error": "client_id must be a positive int"})
        if not isinstance(secret, str) or not secret:
            return self._json(400, {"error": "client_secret must be non-empty"})
        s = load_settings()
        s.api.client_id = cid
        s.api.client_secret = secret
        save_settings(s)
        return self._json(200, {"ok": True})

    def _auth_callback(self, query: str):
        qs = urllib.parse.parse_qs(query or "")
        err = (qs.get("error", [""])[0] or "")
        if err:
            desc = (qs.get("error_description", [""])[0] or "")
            detail = f": {desc}" if desc else ""
            page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                    "<title>Authorization failed</title></head><body>"
                    "<h1>Authorization failed</h1>"
                    f"<p>Authorization failed/denied: {html.escape(err)}"
                    f"{html.escape(detail)}</p>"
                    "<p>You can close this tab and return to osu! Librarian.</p>"
                    "</body></html>")
            return self._send_html(400, page)
        code = (qs.get("code", [""])[0] or "")
        if not code:
            page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                    "<title>Authorization failed</title></head><body>"
                    "<h1>Authorization failed</h1>"
                    "<p>missing code</p>"
                    "<p>You can close this tab and return to osu! Librarian.</p>"
                    "</body></html>")
            return self._send_html(400, page)
        ok, result = _finish_auth(code)
        if ok:
            assert isinstance(result, dict)
            display = str(result.get("username") or result.get("user_id") or "")
            page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                    "<title>Account linked</title></head><body>"
                    f"<h1>Account linked as {html.escape(display)}</h1>"
                    "<p>You can close this tab and return to osu! Librarian.</p>"
                    "</body></html>")
            return self._send_html(200, page)
        reason = result if isinstance(result, str) else "link failed"
        page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                "<title>Authorization failed</title></head><body>"
                "<h1>Authorization failed</h1>"
                f"<p>{html.escape(reason)}</p>"
                "<p>You can close this tab and return to osu! Librarian.</p>"
                "</body></html>")
        return self._send_html(400, page)

    def _auth_relay_return(self, query: str):
        """Relay one-click return: ?ticket=.. -> fetch token, save, HTML page."""
        qs = urllib.parse.parse_qs(query or "")
        ticket = (qs.get("ticket", [""])[0] or "")

        def _fail(reason: str):
            if "unreachable" in reason or "bad relay response" in reason:
                hint = "<p>Check your connection and retry.</p>"
            elif "expired or already used" in reason:
                hint = "<p>This link expired or was already used. Click the link again to get a fresh link.</p>"
            else:
                hint = ""
            page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                    "<title>Authorization failed</title></head><body>"
                    "<h1>Authorization failed</h1>"
                    f"<p>{html.escape(reason)}</p>"
                    f"{hint}"
                    "<p>You can close this tab and return to osu! Librarian.</p>"
                    "<script>try{if(window.opener){window.opener.postMessage({type:\"osu-librarian-linked\"},location.origin);}}catch(e){}</script>"
                    "</body></html>")
            return self._send_html(400, page)

        if not ticket or not _TICKET_RE.match(ticket):
            return _fail("invalid ticket")
        relay_url, relay_cid = _relay_settings()
        if not relay_url or not relay_cid:
            return _fail("linking service not configured")
        ok, tok_or_err = _fetch_relay_token(relay_url, ticket)
        if not ok:
            reason = tok_or_err if isinstance(tok_or_err, str) else "link failed"
            return _fail(reason)
        assert isinstance(tok_or_err, dict)
        ok2, result = _persist_token(tok_or_err)
        if not ok2:
            reason = result if isinstance(result, str) else "link failed"
            return _fail(reason)
        assert isinstance(result, dict)
        display = str(result.get("username") or result.get("user_id") or "")
        extra = ""
        if result.get("linked_without_identity"):
            extra = "<p>Linked without account identity. Reopen osu! Librarian to refresh.</p>"
        page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                "<title>Account linked</title></head><body>"
                f"<h1>Account linked as {html.escape(display)}</h1>"
                f"{extra}"
                "<p>Account linked. Close this tab and return to osu! Librarian.</p>"
                "<script>try{if(window.opener){window.opener.postMessage({type:\"osu-librarian-linked\"},location.origin);}}catch(e){}</script>"
                "</body></html>")
        return self._send_html(200, page)

    def _export(self, body: dict):
        from . import export as exportmod
        fmt = body.get("format", "json")
        if fmt not in exportmod.EXPORTERS:
            return self._json(400, {"error": "format must be json|txt|collection"})
        s = load_settings()
        _keys, rows, _fp = cachemod.load_scan(get_mode())
        rows = _clean_rows(rows)
        # missing/None `ids` means the whole library; [] means empty export.
        ids = body.get("ids")
        if ids is None:
            sel = rows
        else:
            if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                return self._json(400, {"error": "ids must be a list of str"})
            want = set(ids)
            sel = [r for r in rows if isinstance(r, dict) and r.get("id") in want]
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

    def _art(self, query: str):
        """GET /api/art?id=<row id>: serve background image for one row.

        stable -> Songs/<folder>/<bg>; lazer -> files/<h0>/<h0:2>/<hash>.
        The lazer layout (files/<hash[0]>/<hash[:2]>/<hash>, SHA-256 hex) was
        verified empirically against the real store: every Beatmap.Hash and
        File.Hash resolves to files/H[0]/H[0:2]/H on disk.
        """
        qs = urllib.parse.parse_qs(query or "")
        row_id = (qs.get("id", [""])[0] or "")
        if not row_id:
            return self._json(404, {"error": "missing id"})
        mode = get_mode()
        try:
            _keys, rows, _fp = cachemod.load_scan(mode)
        except Exception:
            rows, _fp = [], {}
        row = _art_row_index(mode, rows, _fp).get(row_id)
        if row is None:
            return self._json(404, {"error": "unknown id"})
        s = load_settings()
        full = ""
        if mode == "lazer":
            h = str(row.get("bg_hash", "") or "")
            if len(h) != 64 or any(c not in "0123456789abcdefABCDEF" for c in h):
                return self._json(404, {"error": "no background"})
            h = h.lower()
            lazer_dir = library_paths(s, "lazer").get("lazer_dir", "") or ""
            base = os.path.join(lazer_dir, "files")
            # Verified layout: files/<h0>/<h0:2>/<hash>
            full = os.path.normpath(os.path.join(base, h[0], h[:2], h))
            if not full.startswith(os.path.normpath(base) + os.sep):
                return self._json(404, {"error": "no background"})
        else:
            folder = str(row.get("folder", "") or "")
            bg = str(row.get("bg", "") or "")
            if not folder or not bg:
                return self._json(404, {"error": "no background"})
            songs = library_paths(s, "stable").get("songs_dir", "") or ""
            if not songs:
                return self._json(404, {"error": "no background"})
            # Never join raw query input; folder/bg come from the scan cache.
            # Basename-only bg + normpath prefix check blocks traversal.
            full = os.path.normpath(os.path.join(songs, folder, os.path.basename(bg)))
            if not full.startswith(os.path.normpath(songs) + os.sep):
                return self._json(404, {"error": "no background"})
        if not full or not os.path.isfile(full):
            return self._json(404, {"error": "no background"})
        # Lazer blobs are extensionless hashes; type by the bg filename.
        ctype, _ = mimetypes.guess_type(str(row.get("bg", "") or ""))
        if not ctype:
            ctype, _ = mimetypes.guess_type(full)
        try:
            st = os.stat(full)
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        except OSError:
            return self._json(404, {"error": "no background"})
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        try:
            f = open(full, "rb")
        except OSError:
            return self._json(404, {"error": "no background"})
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(st.st_size))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "public,max-age=86400")
        self.end_headers()
        # Stream in chunks so big backgrounds never sit fully in RAM.
        try:
            with f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (OSError, BrokenPipeError, ConnectionResetError):
            pass

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
