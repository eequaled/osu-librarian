"""Auto-detect osu! stable / lazer installations. Stdlib only.

Detection = check well-known locations, then verify with marker files:
  stable: Songs/ dir (strong) and/or osu!.db (medium)
  lazer:  files/ dir (strong) and/or client.realm (medium)
A candidate needs at least one marker. Results carry a confidence score so the
UI can offer the best hit first.
"""
from __future__ import annotations

import glob
import os
import sys
from dataclasses import dataclass


@dataclass
class Install:
    kind: str            # "stable" | "lazer"
    path: str            # install root
    label: str           # human location, e.g. "default Windows folder"
    songs: bool = False
    osu_db: bool = False
    files: bool = False
    realm: bool = False
    score: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _wine_prefixes(home: str) -> list[str]:
    """All Wine prefix roots worth checking on Linux/macOS."""
    cands = [os.path.join(home, ".wine")]
    cands += sorted(glob.glob(os.path.join(home, ".wine*")))
    cands += sorted(glob.glob(os.path.join(home, "Games", "osu*")))
    # Bottles (native + flatpak)
    cands += sorted(glob.glob(os.path.join(home, ".local", "share", "bottles", "bottles", "*")))
    cands += sorted(glob.glob(os.path.join(home, ".var", "app", "com.usebottles.bottles",
                                           "data", "bottles", "bottles", "*")))
    # generic Bottles custom paths + Lutris runners
    cands += sorted(glob.glob(os.path.join(home, ".local", "share", "lutris",
                                           "runners", "wine", "*")))
    # explicit custom prefix
    if os.environ.get("WINEPREFIX"):
        cands.append(os.environ["WINEPREFIX"])
    # Steam/Proton prefixes (osu! added as a non-Steam game, or Proton-GE runs):
    # <steam>/steamapps/compatdata/<id>/pfx
    for steam in (os.path.join(home, ".local", "share", "Steam"),
                  os.path.join(home, ".steam", "steam")):
        cands += sorted(glob.glob(os.path.join(steam, "steamapps", "compatdata", "*", "pfx")))
    out, seen = [], set()
    for pre in cands:
        pre = os.path.normpath(pre)
        if pre not in seen and os.path.isdir(pre):
            seen.add(pre)
            out.append(pre)
    return out


def _stable_candidates() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            out.append((os.path.join(local, "osu!"), "default install folder"))
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env, "")
            if base:
                out.append((os.path.join(base, "osu!"), "Program Files"))
        for drive in ("C:\\", "D:\\", "E:\\"):
            out.append((os.path.join(drive, "osu!"), "drive root"))
    elif sys.platform == "darwin":
        out.append(("/Applications/osu!.app/Contents/Resources/drive_c/Program Files/osu!",
                    "osu!.app bundle"))
        out.append((os.path.join(home, "Applications/osu!.app/Contents/Resources/drive_c/osu!"),
                    "user Applications"))
    # wine prefixes (linux + mac): drive_c/users/*/AppData/Local/osu!
    for pre in _wine_prefixes(home):
        users = glob.glob(os.path.join(pre, "drive_c", "users", "*", "AppData",
                                       "Local", "osu!"))
        for u in sorted(users):
            out.append((u, f"wine prefix {os.path.basename(pre)}"))
        for sub in ("drive_c/osu!", "drive_c/Program Files/osu!"):
            full = os.path.join(pre, sub)
            if os.path.isdir(full):
                out.append((full, f"wine prefix {os.path.basename(pre)}"))
    out.append((os.path.join(home, "osu!"), "home folder"))
    out.append((os.path.join(home, ".local", "share", "osu-stable"), "data folder"))
    return out


def _lazer_candidates() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA", "")
        if base:
            out.append((os.path.join(base, "osu"), "default data folder"))
    elif sys.platform == "darwin":
        out.append((os.path.join(home, "Library", "Application Support", "osu"),
                    "default data folder"))
    else:
        xdg = os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
        out.append((os.path.join(xdg, "osu"), "default data folder"))
        # flatpak (sh.ppy.osu): data lands under the sandbox
        out.append((os.path.join(home, ".var", "app", "sh.ppy.osu", "data", "osu"),
                    "flatpak data"))
        out.append((os.path.join(home, ".var", "app", "sh.ppy.osu", ".local",
                                 "share", "osu"), "flatpak data"))
        # Steam + custom installs people commonly use
        out.append((os.path.join(home, "osu"), "home folder"))
        out.append((os.path.join(home, "Games", "osu"), "Games folder"))
    return out


def find_installs() -> list[Install]:
    found: list[Install] = []
    seen: set[str] = set()
    for path, label in _stable_candidates():
        if not path:
            continue
        real = os.path.normcase(os.path.normpath(path))
        if real in seen or not os.path.isdir(path):
            continue
        songs = os.path.isdir(os.path.join(path, "Songs"))
        db = os.path.isfile(os.path.join(path, "osu!.db"))
        if not (songs or db):
            continue
        seen.add(real)
        found.append(Install(kind="stable", path=os.path.normpath(path), label=label,
                             songs=songs, osu_db=db,
                             score=(2 if songs else 0) + (1 if db else 0)))
    for path, label in _lazer_candidates():
        if not path:
            continue
        real = os.path.normcase(os.path.normpath(path))
        if real in seen or not os.path.isdir(path):
            continue
        files = os.path.isdir(os.path.join(path, "files"))
        realm = os.path.isfile(os.path.join(path, "client.realm"))
        if not (files or realm):
            continue
        seen.add(real)
        found.append(Install(kind="lazer", path=os.path.normpath(path), label=label,
                             files=files, realm=realm,
                             score=(2 if files else 0) + (1 if realm else 0)))
    found.sort(key=lambda i: (-i.score, i.kind, i.path))
    return found
