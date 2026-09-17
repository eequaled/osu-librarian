"""Build a tiny fake osu!stable install for smoke tests (no game needed)."""
from __future__ import annotations

import os

SONG_A = """osu file format v14
[General]
Mode: 0
[Metadata]
Title:Test Song Alpha
TitleUnicode:Test Song Alpha
Artist:Mapper One
ArtistUnicode:Mapper One
Creator:Mapper One
Version:Insane
Source:
Tags:demo test
BeatmapID:111
BeatmapSetID:1001
[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
[Difficulty]
[TimingPoints]
0,500,4,2,0,80,1,0
[HitObjects]
256,192,1000,1,0,0:0:0:0:
256,192,90000,1,0,0:0:0:0:
"""

SONG_B = SONG_A.replace("Version:Insane", "Version:Normal").replace("BeatmapID:111", "BeatmapID:112")
SONG_C = (SONG_A.replace("Test Song Alpha", "Other Track").replace("Mapper One", "Mapper Two")
          .replace("BeatmapSetID:1001", "BeatmapSetID:1002").replace("BeatmapID:111", "BeatmapID:113")
          .replace("Mode: 0", "Mode: 3"))


def make_mock_install(root: str) -> dict:
    songs = os.path.join(root, "Songs")
    os.makedirs(os.path.join(songs, "1001 Mapper One - Test Song Alpha"), exist_ok=True)
    os.makedirs(os.path.join(songs, "1002 Mapper Two - Other Track"), exist_ok=True)
    with open(os.path.join(songs, "1001 Mapper One - Test Song Alpha", "diff-insane.osu"), "w", encoding="utf-8") as f:
        f.write(SONG_A)
    with open(os.path.join(songs, "1001 Mapper One - Test Song Alpha", "diff-normal.osu"), "w", encoding="utf-8") as f:
        f.write(SONG_B)
    with open(os.path.join(songs, "1002 Mapper Two - Other Track", "diff.osu"), "w", encoding="utf-8") as f:
        f.write(SONG_C)
    return {"songs": songs}


if __name__ == "__main__":
    import sys
    print(make_mock_install(sys.argv[1] if len(sys.argv) > 1 else "/tmp/mock_osu"))
