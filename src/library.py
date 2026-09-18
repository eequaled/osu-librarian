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
    # -- ID CONTRACT (see also src/export.py) --
    # `id`      : dedupe/selection key. stable = .osu md5 (32 lower-hex) or,
    #             when the hash is unavailable, the .osu path fallback (NOT
    #             exportable); lazer = content md5 of the hashed blob, or the
    #             blob path fallback when hashing fails. Never assume 32 chars.
    # `beatmap_id`: online (website) id, int, -1/0/None = local only. Used for
    #             online checks + text search only; never a join/export key.
    # `set_id`  : grouping key for the carousel. stable = str(osu!.db set id)
    #             or "local:<folder>" / "missing:<folder>"; lazer = str(set id)
    #             or "hash:<blob12>". Opaque string: compare exactly, never int().
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


# ---- PARITY CONTRACT: apply_filters <-> web/store.js visibleMaps ----
# The JS mirror is intentional duplication (offline UI needs sync filtering).
# Any change here MUST be mirrored in visibleMaps and vice versa. Exact
# semantics (test agents: build the parity matrix from this list):
# 1. text  : q = f.text.strip().lower() (py) / (f.q||"").trim().toLowerCase()
#            (js). Empty q matches all. Else substring of search_blob, where
#            search_blob = "artist title creator diff source tags beatmap_id"
#            joined with single spaces, lowercased. beatmap_id via str() (py)
#            / String(m.beatmap_id ?? "") (js); None/missing renders as
#            "None"/"" but still searched. Case-insensitive substring
#            (`in` / `includes`).
# 2. played: f.played "all" = no filter (any other unknown value also passes).
#            "played" keeps rows with b.played == (played_local or
#            played_online) truthy (js isPlayed(m)). "unplayed" keeps rows
#            with not played. played is derived, never stored.
# 3. mode  : f.mode "all" = no filter. Else exact string equality
#            b.mode_name == f.mode ("osu"|"taiko"|"catch"|"mania"). Numeric
#            b.mode is IGNORED here. Case-sensitive.
# 4. stars : st = b.stars or 0.0 (None/""/0 -> 0.0). Keep iff
#            star_min <= st <= star_max (py f.star_min/f.star_max, js f.smin/
#            f.smax, inclusive both ends). STAR-ZERO EDGE: when st == 0.0
#            (unknown SR) the row still matches iff star_min <= 0.0, even if
#            star_max < 0 would otherwise reject (i.e. `(st==0 and smin<=0)`
#            rescues). NaN stars behave as reject (comparisons false, and
#            NaN != 0.0 so no rescue).
# 5. rank  : f.ranked "all" = no filter. Else exact string equality
#            b.ranked == f.ranked (case-sensitive; "unknown" is a real value
#            that only matches filter "unknown", never "all"'s opposite).
# 6. sort  : stable ascending sort. Keys -- title:(title.lower,diff.lower),
#            artist:(artist.lower,title.lower), creator:(creator.lower,
#            title.lower), bpm:bpm, length:length_ms, stars:stars,
#            rank:(not played, grade or "zz"). Unknown sort falls back to
#            title. JS SORTERS match (localeCompare ~ lower + diffCmp tie-
#            break; numeric subs for bpm/length/stars; rank compares
#            isPlayed-first then grade||"zz"). Python sorted() is stable.
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
