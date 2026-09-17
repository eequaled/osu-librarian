#!/usr/bin/env python3
"""osu! Librarian MVP — CLI: scan / report / ui / check-online / auth-helper."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_settings  # noqa: E402
from src.lazer_scanner import scan_lazer  # noqa: E402
from src.library import from_dict_list, summarize, to_dict_list  # noqa: E402
from src.osu_api import authorize_url, client_credentials, exchange_code, mark_online_played  # noqa: E402
from src.report import build_report  # noqa: E402
from src.stable_scanner import scan_stable  # noqa: E402


def cmd_scan(a) -> int:
    s = load_settings()
    mode = a.mode or s.mode
    if mode == "lazer":
        lazer_dir = a.lazer_dir or s.lazer_dir
        maps = scan_lazer(lazer_dir, a.realm_export or s.realm_export)
    else:
        songs = a.songs or s.songs_dir
        osu_db = a.db if a.db is not None else s.osu_db
        scores = a.scores if a.scores is not None else s.scores_db
        if not songs and s.stable_dir:
            songs = os.path.join(s.stable_dir, "Songs")
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
    print("\naccess token OK. Add to your API calls; refresh_token below (keep secret):")
    print(json.dumps({k: tok.get(k) for k in ("access_token", "refresh_token", "expires_in")}, indent=1)[:400] + "…")
    if a.save_token:
        with open(a.save_token, "w", encoding="utf-8") as f:
            json.dump(tok, f, indent=1)
        print(f"saved -> {a.save_token}")
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

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
