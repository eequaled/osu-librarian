"""osu! API v2 client (stdlib urllib only).

Auth: OAuth Authorization Code flow. Steps:
  1. Register app at https://osu.ppy.sh/home/account/edit (OAuth section)
     -> client_id, client_secret, redirect_uri.
  2. Open authorize URL, approve, copy `code`.
  3. Exchange code -> access_token (cached in .token_cache.json).
  4. GET /api/v2/beatmaps/{id}/scores/users/{user}/all -> [] means unplayed.

Bulk `mark_online_played()` caches per-beatmap results in .api_cache.json and
sleeps to respect rate limits. Only beatmaps with beatmap_id > 0 can be
checked (local-only maps have no online identity).
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

API_BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
AUTH_URL = "https://osu.ppy.sh/oauth/authorize"


def authorize_url(client_id: int, redirect_uri: str) -> str:
    q = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": "identify public",
    })
    return f"{AUTH_URL}?{q}"


def exchange_code(client_id: int, client_secret: str, redirect_uri: str, code: str) -> dict:
    return _post(TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret,
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    })


def refresh_token(client_id: int, client_secret: str, refresh: str) -> dict:
    return _post(TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "refresh_token", "refresh_token": refresh,
    })


def client_credentials(client_id: int, client_secret: str) -> dict:
    # public-only token; CANNOT read user scores, but fine for beatmap lookups.
    return _post(TOKEN_URL, {
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "client_credentials", "scope": "public",
    })


def _post(url: str, data: dict) -> dict:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode(),
        headers={"Accept": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get(url: str, token: str) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()
        except Exception:
            body = ""
        return e.code, {"error": body}


def user_has_scores(beatmap_id: int, user_id: int, token: str, ruleset: str = "") -> bool | None:
    """True/False, or None on transport/auth error (caller keeps old value)."""
    url = f"{API_BASE}/beatmaps/{beatmap_id}/scores/users/{user_id}/all"
    if ruleset:
        url += f"?ruleset={urllib.parse.quote(ruleset)}"
    status, body = _get(url, token)
    if status == 200 and isinstance(body, dict):
        return bool(body.get("scores"))
    if status == 404:
        return False
    return None


def lookup_by_md5(md5: str, token: str) -> int:
    status, body = _get(f"{API_BASE}/beatmaps/lookup?checksum={md5}", token)
    if status == 200 and isinstance(body, dict):
        try:
            return int(body.get("id", -1))
        except (TypeError, ValueError):
            return -1
    return -1


def load_cache(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def mark_online_played(maps: list, user_id: int, token: str,
                       cache_path: str = ".api_cache.json",
                       sleep_s: float = 0.4) -> dict:
    """Mutate maps in place (sets played_online). Returns stats dict."""
    cache = load_cache(cache_path)
    stats = {"checked": 0, "from_cache": 0, "played": 0, "errors": 0, "skipped_no_id": 0}
    for b in maps:
        bid = getattr(b, "beatmap_id", -1)
        if not bid or bid <= 0:
            stats["skipped_no_id"] += 1
            continue
        key = str(bid)
        if key in cache:
            has = bool(cache[key])
            stats["from_cache"] += 1
        else:
            has = user_has_scores(bid, user_id, token)
            stats["checked"] += 1
            time.sleep(sleep_s)
            if has is None:
                stats["errors"] += 1
                continue
            cache[key] = bool(has)
            if stats["checked"] % 20 == 0:
                save_cache(cache_path, cache)
        b.played_online = bool(has)
        if has:
            stats["played"] += 1
    save_cache(cache_path, cache)
    return stats
