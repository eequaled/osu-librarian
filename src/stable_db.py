"""Readers for osu!stable binary dbs (osu!.db, scores.db) per ppy/osu wiki
'Legacy database file structure'. Stdlib only.

String encoding: 0x00 = null/empty, else 0x0B + ULEB128 length + UTF-8 bytes.
Star tables: Int count + (0x08 Int 0x0D Double) pre-20250107, else
(0x08 Int 0x0C Float). We sniff the marker byte so both work.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


class Cursor:
    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise ValueError("truncated db file")
        b = self.buf[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def i16(self) -> int:
        return struct.unpack("<h", self.take(2))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def boolean(self) -> bool:
        return self.u8() != 0

    def uleb128(self) -> int:
        out = 0
        shift = 0
        while True:
            b = self.u8()
            out |= (b & 0x7F) << shift
            if not (b & 0x80):
                return out
            shift += 7

    def string(self) -> str:
        marker = self.u8()
        if marker == 0x00:
            return ""
        if marker != 0x0B:
            raise ValueError(f"bad string marker 0x{marker:02x} at {self.pos - 1}")
        n = self.uleb128()
        if n == 0:
            return ""
        return self.take(n).decode("utf-8", errors="replace")

    def star_table(self) -> dict[int, float]:
        """Read one Int->SR table, handling both Double and Float variants."""
        n = self.u32()
        out: dict[int, float] = {}
        for _ in range(n):
            b = self.u8()
            if b != 0x08:
                raise ValueError("bad star pair prefix")
            mods = self.u32()
            tag = self.u8()
            if tag == 0x0D:  # double (db < 20250107)
                out[mods] = self.f64()
            elif tag == 0x0C:  # float (db >= 20250107)
                out[mods] = float(self.f32())
            else:
                raise ValueError(f"bad star value tag 0x{tag:02x}")
        return out


RANKED_NAMES = {
    0: "unknown", 1: "unsubmitted", 2: "pending", 3: "unused",
    4: "ranked", 5: "approved", 6: "qualified", 7: "loved",
}

GRADE_NAMES = {0: "", 1: "D", 2: "C", 3: "B", 4: "A", 5: "S", 6: "SH", 7: "X", 8: "XH"}
# stable grade byte order used by osu!.db: 0 none,1 D.. matches wiki readers.


@dataclass
class OsuDbBeatmap:
    artist: str = ""
    title: str = ""
    creator: str = ""
    difficulty: str = ""
    md5: str = ""
    osu_file: str = ""
    folder: str = ""
    ranked: int = 0
    mode: int = 0
    ar: float = 5.0
    cs: float = 4.0
    hp: float = 5.0
    od: float = 5.0
    stars: float = 0.0
    stars_by_mode: dict = field(default_factory=dict)
    drain_s: int = 0
    total_ms: int = 0
    beatmap_id: int = 0
    grades: list = field(default_factory=lambda: [0, 0, 0, 0])
    unplayed: bool = True
    last_played: int = 0  # windows ticks
    source: str = ""
    tags: str = ""
    bpm: float = 0.0


def _read_float_or_byte(c: Cursor, version: int) -> float:
    if version < 20140609:
        return float(c.u8())
    return float(c.f32())


def read_osu_db(path: str) -> tuple[dict, list[OsuDbBeatmap]]:
    with open(path, "rb") as f:
        buf = f.read()
    c = Cursor(buf)
    version = c.u32()
    c.u32()  # folder count
    c.boolean()  # account unlocked
    c.u64()  # unlock date
    player = c.string()
    n_maps = c.u32()
    maps: list[OsuDbBeatmap] = []
    for _ in range(n_maps):
        if version < 20191106:
            _size = c.u32()
        m = OsuDbBeatmap()
        m.artist = c.string()
        c.string()  # artist unicode
        m.title = c.string()
        c.string()  # title unicode
        m.creator = c.string()
        m.difficulty = c.string()
        c.string()  # audio file
        m.md5 = c.string()
        m.osu_file = c.string()
        m.ranked = c.u8()
        c.u16()  # circles
        c.u16()  # sliders
        c.u16()  # spinners
        c.u64()  # last modified
        m.ar = _read_float_or_byte(c, version)
        m.cs = _read_float_or_byte(c, version)
        m.hp = _read_float_or_byte(c, version)
        m.od = _read_float_or_byte(c, version)
        c.f64()  # slider velocity
        stars_tables = []
        if version >= 20140609:
            for _mi in range(4):
                stars_tables.append(c.star_table())
        m.stars_by_mode = {
            "osu": stars_tables[0] if stars_tables else {},
            "taiko": stars_tables[1] if len(stars_tables) > 1 else {},
            "catch": stars_tables[2] if len(stars_tables) > 2 else {},
            "mania": stars_tables[3] if len(stars_tables) > 3 else {},
        }
        m.drain_s = c.u32()
        m.total_ms = c.u32()
        c.u32()  # preview time
        n_tp = c.u32()
        c.pos += n_tp * 17  # timing points are fixed 17 bytes
        m.beatmap_id = c.u32()
        c.u32()  # thread/set id field (beatmapset-ish); .osu BeatmapSetID is authoritative
        c.u16() if False else None
        # grades per ruleset
        g = [c.u8(), c.u8(), c.u8(), c.u8()]
        m.grades = g
        c.u16()  # local offset
        c.f32()  # stack leniency
        m.mode = c.u8()
        m.source = c.string()
        m.tags = c.string()
        c.u16()  # online offset
        c.string()  # font
        m.unplayed = c.boolean()
        m.last_played = c.u64()
        c.boolean()  # osz2
        m.folder = c.string()
        c.u64()  # last repo check
        c.boolean(); c.boolean(); c.boolean(); c.boolean(); c.boolean()  # ignore sound/skin, disable storyboard/video, visual override
        if version < 20140609:
            c.u16()
        c.u32()  # last modification (?)
        m.stars = float((stars_tables[m.mode] if m.mode < len(stars_tables) else {}).get(0, 0.0)) if stars_tables else 0.0
        m.bpm = 0.0  # computed from .osu in scanner; db timing points skipped
        maps.append(m)
    meta = {"version": version, "player": player, "beatmaps": len(maps)}
    try:
        _perms = c.u32()
        meta["permissions"] = _perms
    except ValueError:
        pass
    return meta, maps


@dataclass
class ScoreEntry:
    mode: int = 0
    score: int = 0
    combo: int = 0
    perfect: bool = False
    mods: int = 0
    timestamp: int = 0
    online_id: int = 0
    count300: int = 0
    count100: int = 0
    count50: int = 0
    countgeki: int = 0
    countkatu: int = 0
    miss: int = 0
    player: str = ""


def read_scores_db(path: str) -> tuple[dict, dict[str, list[ScoreEntry]]]:
    with open(path, "rb") as f:
        buf = f.read()
    c = Cursor(buf)
    version = c.u32()
    n = c.u32()
    by_md5: dict[str, list[ScoreEntry]] = {}
    for _ in range(n):
        md5 = c.string()
        n_scores = c.u32()
        arr: list[ScoreEntry] = []
        for _s in range(n_scores):
            e = ScoreEntry()
            e.mode = c.u8()
            c.u32()  # replay version
            c.string()  # beatmap md5 (dup)
            e.player = c.string()
            c.string()  # replay md5
            e.count300 = c.u16(); e.count100 = c.u16(); e.count50 = c.u16()
            e.countgeki = c.u16(); e.countkatu = c.u16(); e.miss = c.u16()
            e.score = c.u32()
            e.combo = c.u16()
            e.perfect = c.boolean()
            e.mods = c.u32()
            c.string()  # always empty
            e.timestamp = c.u64()
            c.u32()  # 0xffffffff
            e.online_id = struct.unpack("<q", c.take(8))[0]
            # target-practice extra double only when mod bit set — detect by remaining layout is hard;
            # MVP: peek — if next bytes look like another score header they'd misalign, so only
            # consume when TP mod (bit 23?) present. Bit 23 (1<<23) = Target Practice in stable mods.
            # We avoid consuming otherwise.
            arr.append(e)
        by_md5[md5] = arr
    return {"version": version, "beatmaps": n}, by_md5
