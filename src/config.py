"""Shared settings: stable|lazer mode + path auto-detection (stdlib only)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field


@dataclass
class ApiSettings:
    client_id: int = 0
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8787/api/auth/callback"
    user_id: int = 0


@dataclass
class Settings:
    mode: str = "stable"  # "stable" | "lazer"
    stable_dir: str = ""
    songs_dir: str = ""
    osu_db: str = ""
    scores_db: str = ""
    lazer_dir: str = ""
    realm_export: str = ""
    api: ApiSettings = field(default_factory=ApiSettings)

    def resolved(self) -> "Settings":
        s = Settings(
            mode=self.mode if self.mode in ("stable", "lazer") else "stable",
            stable_dir=self.stable_dir or default_stable_dir(),
            songs_dir=self.songs_dir,
            osu_db=self.osu_db,
            scores_db=self.scores_db,
            lazer_dir=self.lazer_dir or default_lazer_dir(),
            realm_export=self.realm_export,
            api=self.api,
        )
        if not s.songs_dir and s.stable_dir:
            cand = os.path.join(s.stable_dir, "Songs")
            s.songs_dir = cand
        if not s.osu_db and s.stable_dir:
            cand = os.path.join(s.stable_dir, "osu!.db")
            s.osu_db = cand if os.path.exists(cand) else ""
        if not s.scores_db and s.stable_dir:
            cand = os.path.join(s.stable_dir, "scores.db")
            s.scores_db = cand if os.path.exists(cand) else ""
        return s


def default_stable_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA", "")
        return os.path.join(base, "osu!") if base else ""
    if sys.platform == "darwin":
        return "/Applications/osu!.app/Contents/Resources/drive_c/Program Files/osu!"
    # linux (stable via wine) — common locations
    for cand in (
        os.path.expanduser("~/.wine/drive_c/users/$USER/Local Settings/Application Data/osu!"),
        os.path.expanduser("~/.local/share/osu-stable"),
    ):
        if os.path.isdir(cand):
            return cand
    return ""


def default_lazer_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA", "")
        return os.path.join(base, "osu") if base else ""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/osu")
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        return os.path.join(xdg, "osu")
    return ""


def load_settings(path: str = "settings.json") -> Settings:
    if not os.path.exists(path):
        return Settings().resolved()
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    api = raw.get("api", {})
    s = Settings(
        mode=raw.get("mode", "stable"),
        stable_dir=raw.get("stable_dir", ""),
        songs_dir=raw.get("songs_dir", ""),
        osu_db=raw.get("osu_db", ""),
        scores_db=raw.get("scores_db", ""),
        lazer_dir=raw.get("lazer_dir", ""),
        realm_export=raw.get("realm_export", ""),
        api=ApiSettings(
            client_id=int(api.get("client_id", 0) or 0),
            client_secret=api.get("client_secret", ""),
            redirect_uri=api.get("redirect_uri", "http://localhost:8787/api/auth/callback"),
            user_id=int(api.get("user_id", 0) or 0),
        ),
    )
    return s.resolved()


def save_settings(s: Settings, path: str = "settings.json") -> None:
    raw = {
        "mode": s.mode,
        "stable_dir": s.stable_dir,
        "songs_dir": s.songs_dir,
        "osu_db": s.osu_db,
        "scores_db": s.scores_db,
        "lazer_dir": s.lazer_dir,
        "realm_export": s.realm_export,
        "api": {
            "client_id": s.api.client_id,
            "client_secret": s.api.client_secret,
            "redirect_uri": s.api.redirect_uri,
            "user_id": s.api.user_id,
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
