#!/usr/bin/env python3
"""osu! Librarian MVP — CLI: scan / report / ui / check-online / auth-helper."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_settings  # noqa: E402
from src.lazer_scanner import scan_lazer  # noqa: E402
from src.library import from_dict_list, summarize, to_dict_list  # noqa: E402
from src.osu_api import authorize_url, client_credentials, exchange_code, get_me, mark_online_played  # noqa: E402
from src.report import build_report  # noqa: E402
from src.stable_scanner import scan_stable  # noqa: E402


def cmd_scan(a) -> int:
    s = load_settings()
    fresh = getattr(a, "fresh", True)
    if not fresh:
        print("[warn] --no-fresh is accepted for API parity but the CLI scan "
              "is always a full scan; ignoring",
              file=sys.stderr)
    mode = a.mode or s.mode
    if mode == "lazer":
        lazer_dir = a.lazer_dir or s.lazer_dir
        if not lazer_dir:
            print("error: no lazer directory given (pass --lazer-dir or set lazer_dir in settings.json); nothing scanned, no output written",
                  file=sys.stderr)
            return 2
        maps = scan_lazer(lazer_dir, a.realm_export or s.realm_export)
    else:
        songs = a.songs or s.songs_dir
        osu_db = a.db if a.db is not None else s.osu_db
        scores = a.scores if a.scores is not None else s.scores_db
        if not songs and s.stable_dir:
            songs = os.path.join(s.stable_dir, "Songs")
        if not songs:
            print("error: no Songs folder given (pass --songs or set songs_dir/stable_dir in settings.json); nothing scanned, no output written",
                  file=sys.stderr)
            return 2
        maps = scan_stable(songs or "", osu_db or "", scores or "")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(to_dict_list(maps), f, indent=1)
    sm = summarize(maps)
    print(f"mode={mode} diffs={sm['diffs']} sets={sm['sets']} "
          f"played={sm['played']} unplayed={sm['unplayed']} -> {a.out}")
    return 0


def cmd_report(a) -> int:
    with open(a.in_path, encoding="utf-8") as f:
        maps = from_dict_list(json.load(f))
    html = build_report(maps)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"report: {len(maps)} diffs -> {a.out}")
    return 0


def cmd_ui(a) -> int:
    from src.app_tk import load_library, run
    run(load_library(a.in_path))
    return 0


def cmd_auth(a) -> int:
    s = load_settings()
    cid = a.client_id or s.api.client_id
    redir = a.redirect_uri or s.api.redirect_uri
    if not cid:
        print("set client_id via --client-id or settings.json (register at osu.ppy.sh/home/account/edit → OAuth)")
        return 2
    print("1) open this URL, approve, copy the ?code= value:\n")
    print("   " + authorize_url(cid, redir) + "\n")
    code = (a.code or input("2) paste code: ").strip())
    sec = a.client_secret or s.api.client_secret
    tok = exchange_code(cid, sec, redir, code)
    obtained_at = time.time()
    if isinstance(tok, dict):
        tok["obtained_at"] = obtained_at
    try:
        me = get_me(tok.get("access_token", "") if isinstance(tok, dict) else "")
        uid = me.get("id", 0) if isinstance(me, dict) else 0
    except Exception:
        uid = 0
    if not uid and isinstance(tok, dict):
        try:
            uid = int(tok.get("user_id", 0) or 0)
        except (TypeError, ValueError):
            uid = tok.get("user_id", 0)
    if a.save_token:
        with open(a.save_token, "w", encoding="utf-8") as f:
            json.dump(tok, f, indent=1)
        print(f"saved -> {a.save_token} (token saved, not printed)")
    print(f"auth OK user_id={uid} obtained_at={obtained_at}")
    return 0


def cmd_check_online(a) -> int:
    with open(a.in_path, encoding="utf-8") as f:
        maps = from_dict_list(json.load(f))
    s = load_settings()
    cid = a.client_id or s.api.client_id
    sec = a.client_secret or s.api.client_secret
    uid = a.user_id or s.api.user_id
    token = a.token
    if not token:
        if a.refresh:
            from src.osu_api import refresh_token as _rt
            token = _rt(cid, sec, a.refresh)["access_token"]
        elif a.code:
            token = exchange_code(cid, sec, s.api.redirect_uri, a.code)["access_token"]
        elif a.public_only:
            token = client_credentials(cid, sec)["access_token"]
            print("[warn] public-only token cannot read user scores; use auth flow for played checks")
        else:
            print("need --token | --code | --refresh | --public-only (run `auth` subcommand first)")
            return 2
    if not uid:
        print("need --user-id (your numeric osu! id)")
        return 2
    stats = mark_online_played(maps, uid, token, cache_path=a.cache)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(to_dict_list(maps), f, indent=1)
    print(f"online check {stats} -> {a.out}")
    return 0


def cmd_web(a) -> int:
    from src.server import serve
    serve(a.port)
    return 0


def cmd_download(a) -> int:
    import hashlib as _hashlib
    from src.download import (build_lazer_import, load_id_file, parse_collection_db,
                              parse_id_list, run_download)
    md5s: list[str] = []
    source_name: str | None = getattr(a, "name", "") or None
    source_collections: list[dict] | None = None
    extra_sets: list[int] = []
    # --db (collection.db upload path)
    if getattr(a, "db", ""):
        try:
            cols = parse_collection_db(a.db)
        except (OSError, ValueError) as e:
            print(f"error: cannot parse collection.db: {e}", file=sys.stderr)
            return 2
        if not cols:
            print("error: collection.db has no collections", file=sys.stderr)
            return 2
        wanted = getattr(a, "collection", "") or ""
        if wanted and wanted != "all":
            cols = [c for c in cols if c["name"] == wanted]
            if not cols:
                print(f"error: collection {wanted!r} not in db", file=sys.stderr)
                return 2
        source_collections = cols
        if len(cols) == 1 and not getattr(a, "name", ""):
            source_name = cols[0]["name"]
        for c in cols:
            md5s.extend(m.lower() for m in c["md5s"] if m)
    # --md5s (file or comma list)
    if getattr(a, "md5s", ""):
        raw = a.md5s
        if os.path.isfile(raw):
            with open(raw, encoding="utf-8") as f:
                parsed = parse_id_list(f.read())
            md5s.extend(parsed["md5s"])
            extra_sets.extend(parsed["sets"])
        else:
            parsed = parse_id_list(raw.replace(",", "\n"))
            md5s.extend(parsed["md5s"])
            extra_sets.extend(parsed["sets"])
    # --ids (file with urls/ids, or inline list)
    if getattr(a, "ids", ""):
        raw = a.ids
        if os.path.isfile(raw):
            parsed = load_id_file(raw)
        else:
            parsed = parse_id_list(raw.replace(",", "\n"))
        md5s.extend(parsed["md5s"])
        extra_sets.extend(parsed["sets"])
    # --sets (comma list, set ids only)
    if getattr(a, "sets", ""):
        for tok in str(a.sets).replace(",", " ").split():
            try:
                extra_sets.append(int(tok))
            except ValueError:
                print(f"error: bad set id {tok!r}", file=sys.stderr)
                return 2
    # dedupe md5s now for the empty check (run_download dedupes again)
    seen: set[str] = set()
    uniq: list[str] = []
    for m in md5s:
        low = m.lower()
        if low not in seen:
            seen.add(low)
            uniq.append(low)
    md5s = uniq
    extra_sets = sorted({s for s in extra_sets if s > 0})
    if not md5s and not extra_sets:
        print("error: nothing to download (give --db, --md5s, --ids or --sets)",
              file=sys.stderr)
        return 2
    jobs = getattr(a, "jobs", 1) or 1
    if jobs > 2:
        print("[warn] --jobs capped at 2 (mirror rate limits); using 2", file=sys.stderr)
    delay = float(getattr(a, "delay", 0.5) or 0.5)
    if delay < 0.5:
        print("[warn] --delay raised to 0.5s (mirror rate limits)", file=sys.stderr)
        delay = 0.5
    out = getattr(a, "out", "") or os.path.join(os.path.expanduser("~"), "Downloads", "osu-maps")
    # one run = one dir: <out>/<name-hash>/ unless --out already looks like a run dir
    run_hash = _hashlib.sha1("\n".join(sorted(md5s)).encode()).hexdigest()[:8]
    run_dir = out
    if getattr(a, "no_run_dir", False):
        run_dir = out
    else:
        # avoid double-nesting when user already passed a run dir
        has_manifest = os.path.isfile(os.path.join(out, "mapping.json"))
        if not has_manifest:
            safe = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in (source_name or "librarian")).strip() or "librarian"
            run_dir = os.path.join(out, f"{safe}-{run_hash}")
    try:
        manifest = run_download(
            md5s=md5s, outdir=run_dir,
            name=getattr(a, "name", "") or None,
            source_name=source_name,
            source_collections=source_collections,
            extra_sets=extra_sets,
            only_list=bool(getattr(a, "only_list", False)),
            lookup_delay_s=delay,
            download_delay_s=max(1.0, delay),
            merge=not bool(getattr(a, "keep_separate", False)),
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"run {manifest['name']}: {manifest['diff_count']} diffs -> "
          f"{len(manifest['set_ids'])} sets (+{len(manifest['sets_extra'])} extra), "
          f"missing={len(manifest['missing'])} -> {run_dir}")
    print(f"collection.db + mapping.json + missing.txt written to {run_dir}")
    if getattr(a, "lazer_import_dir", ""):
        info = build_lazer_import(run_dir, a.lazer_import_dir)
        print(f"lazer-import folder: {info['osz']} osz -> {info['dir']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="osu-librarian", description="osu! stable/lazer local library manager (MVP)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="scan local library -> library.json")
    p.add_argument("--mode", choices=["stable", "lazer"], default="")
    p.add_argument("--songs", default="")
    p.add_argument("--db", default=None)
    p.add_argument("--scores", default=None)
    p.add_argument("--lazer-dir", default="")
    p.add_argument("--realm-export", default="")
    p.add_argument("--out", default="library.json")
    p.add_argument("--fresh", dest="fresh", action="store_true", default=True,
                   help="force a full rescan (the CLI scan is always full; "
                        "kept for parity with POST /api/scan {fresh})")
    p.add_argument("--no-fresh", dest="fresh", action="store_false",
                   help="accepted for API parity; the CLI scan is always a "
                        "full scan, so this flag is ignored with a warning")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("report", help="library.json -> filterable HTML")
    p.add_argument("--in", dest="in_path", default="library.json")
    p.add_argument("--out", default="report.html")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("ui", help="tkinter desktop UI")
    p.add_argument("--in", dest="in_path", default="library.json")
    p.set_defaults(fn=cmd_ui)

    p = sub.add_parser("auth", help="OAuth helper: print authorize URL, exchange code")
    p.add_argument("--client-id", type=int, default=0)
    p.add_argument("--client-secret", default="")
    p.add_argument("--redirect-uri", default="")
    p.add_argument("--code", default="")
    p.add_argument("--save-token", default="")
    p.set_defaults(fn=cmd_auth)

    p = sub.add_parser("web", help="serve the web UI + JSON API locally")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(fn=cmd_web)

    p = sub.add_parser("check-online", help="mark online-played via API v2")
    p.add_argument("--in", dest="in_path", default="library.json")
    p.add_argument("--out", default="library.online.json")
    p.add_argument("--client-id", type=int, default=0)
    p.add_argument("--client-secret", default="")
    p.add_argument("--user-id", type=int, default=0)
    p.add_argument("--token", default="")
    p.add_argument("--code", default="")
    p.add_argument("--refresh", default="")
    p.add_argument("--public-only", action="store_true")
    p.add_argument("--cache", default=".api_cache.json")
    p.set_defaults(fn=cmd_check_online)

    p = sub.add_parser("download", help="resolve + download .osz from collection.db / md5 / set ids (always emits collection.db)")
    p.add_argument("--db", default="", help="stable collection.db path")
    p.add_argument("--collection", default="", help="collection name within --db (default: all merged)")
    p.add_argument("--md5s", default="", help="md5 list file (.txt) or comma-separated md5s")
    p.add_argument("--ids", default="", help="id list file (.txt/.json) or pasted URLs/ids")
    p.add_argument("--sets", default="", help="comma-separated beatmapset ids")
    p.add_argument("--name", default="", help="run/collection name (default: source name or Librarian <date> <hash>)")
    p.add_argument("--out", default="", help="output dir (default ~/Downloads/osu-maps); a run subdir is created")
    p.add_argument("--no-run-dir", action="store_true", help="write directly into --out instead of a run subdir")
    p.add_argument("--only-list", action="store_true", help="resolve + write mapping + collection.db, no bytes")
    p.add_argument("--keep-separate", action="store_true", help="multi-collection .db -> N collections instead of merging")
    p.add_argument("--jobs", type=int, default=1, help="lookup workers (capped at 2; sequential pacing always applies)")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between lookups (min 0.5)")
    p.add_argument("--lazer-import-dir", default="", help="also build a fake-stable lazer import folder here")
    p.set_defaults(fn=cmd_download)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
