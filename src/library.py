"""Unified in-memory model + filtering/grouping/selection.

This mirrors osu! song-select concepts:
- view "mapset"    = grouped by song (one row per set, like default carousel)
- view "difficulty" = flat, one row per diff (like 'No grouping' + sort)
- filters: text, played, mode, star range, ranked status
- multiselect: explicit id set + select-all-filtered / invert helpers
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Beatmap:
    id: str                      # stable: md5 | lazer: sha/md5/onlineid fallback
    set_id: str                  # grouping key
    artist: str = ""
    title: str = ""
    creator: str = ""
    diff: str = ""
    source: str = ""
    tags: str = ""
    mode: int = 0
    mode_name: str = "osu"
    ar: float = 5.0
    cs: float = 4.0
    od: float = 5.0
    hp: float = 5.0
    bpm: float = 0.0
    stars: float = 0.0
    length_ms: int = 0
    beatmap_id: int = -1         # online id, -1 = local only
    ranked: str = "unknown"
    played_local: bool = False
    played_online: bool = False
    grade: str = ""
    last_played: int = 0
    score_count: int = 0
    folder: str = ""
    origin: str = "stable"       # stable | lazer
    bg: str = ""
    bg_hash: str = ""
    date_added: str = ""

    @property
    def played(self) -> bool:
        return self.played_local or self.played_online

    def search_blob(self) -> str:
        return " ".join([
            self.artist, self.title, self.creator, self.diff,
            self.source, self.tags, str(self.beatmap_id),
        ]).lower()


@dataclass
class Mapset:
    key: str
    title: str = ""
    artist: str = ""
    creator: str = ""
    beatmaps: list[Beatmap] = field(default_factory=list)

    @property
    def played_count(self) -> int:
        return sum(1 for b in self.beatmaps if b.played)

    @property
    def max_stars(self) -> float:
        return max((b.stars for b in self.beatmaps), default=0.0)


@dataclass
class Filters:
    text: str = ""
    played: str = "all"          # all | played | unplayed
    mode: str = "all"            # all | osu | taiko | catch | mania
    star_min: float = 0.0
    star_max: float = 99.0
    ranked: str = "all"          # all | ranked | loved | qualified | pending | ...
    sort: str = "title"          # title|artist|creator|bpm|length|stars|rank


def apply_filters(maps: list[Beatmap], f: Filters) -> list[Beatmap]:
    q = f.text.strip().lower()
    out = []
    for b in maps:
        if f.played == "played" and not b.played:
            continue
        if f.played == "unplayed" and b.played:
            continue
        if f.mode != "all" and b.mode_name != f.mode:
            continue
        if not (f.star_min <= (b.stars or 0.0) <= f.star_max):
            # maps with unknown SR (0.0) still match a 0-min filter
            if not (b.stars == 0.0 and f.star_min <= 0.0):
                continue
        if f.ranked != "all" and b.ranked != f.ranked:
            continue
        if q and q not in b.search_blob():
            continue
        out.append(b)
    key = {
        "title": lambda b: (b.title.lower(), b.diff.lower()),
        "artist": lambda b: (b.artist.lower(), b.title.lower()),
        "creator": lambda b: (b.creator.lower(), b.title.lower()),
        "bpm": lambda b: b.bpm,
        "length": lambda b: b.length_ms,
        "stars": lambda b: b.stars,
        "rank": lambda b: (not b.played, b.grade or "zz"),
    }.get(f.sort, lambda b: (b.title.lower(), b.diff.lower()))
    return sorted(out, key=key)


def group_mapsets(maps: list[Beatmap]) -> list[Mapset]:
    order: dict[str, Mapset] = {}
    for b in maps:
        ms = order.get(b.set_id)
        if ms is None:
            ms = Mapset(key=b.set_id, title=b.title, artist=b.artist, creator=b.creator)
            order[b.set_id] = ms
        ms.beatmaps.append(b)
    for ms in order.values():
        ms.beatmaps.sort(key=lambda b: b.stars)
    return sorted(order.values(), key=lambda m: (m.artist.lower(), m.title.lower()))


def summarize(maps: list[Beatmap]) -> dict:
    return {
        "diffs": len(maps),
        "sets": len({m.set_id for m in maps}),
        "played": sum(1 for m in maps if m.played),
        "unplayed": sum(1 for m in maps if not m.played),
    }


# ---- (de)serialisation for library.json ----

def to_dict_list(maps: list[Beatmap]) -> list[dict]:
    return [b.__dict__ for b in maps]


def from_dict_list(rows: list[dict]) -> list[Beatmap]:
    out = []
    for r in rows:
        known = {k: r.get(k, v) for k, v in Beatmap(
            id="", set_id="",
        ).__dict__.items()}
        known.update({k: v for k, v in r.items() if k in known})
        out.append(Beatmap(**known))
    return out
