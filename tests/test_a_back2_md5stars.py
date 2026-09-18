import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.lazer_scanner import normalize_realm_dump, parse_lazer_blob

MD5_X = "d" * 32

OSU_TMPL = """osu file format v14
[General]
Mode: 0
[Metadata]
Title:T
Artist:A
Creator:C
Version:{diff}
BeatmapID:{bid}
BeatmapSetID:{sid}
[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
[TimingPoints]
0,500,4,2,0,80,1,0
[HitObjects]
256,192,1000,1,0,0:0:0:0:
"""


class Md5StarsTests(unittest.TestCase):
    def test_md5_only_stars_indexed(self):
        raw = {"beatmaps": [{"onlineId": -1, "md5": MD5_X, "stars": 4.25}],
               "scores": [], "sets": {}}
        r = normalize_realm_dump(raw)
        self.assertIn(MD5_X, r["stars"])
        self.assertAlmostEqual(r["stars"][MD5_X], 4.25)

    def test_md5_star_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            with open(p, "w", encoding="utf-8") as f:
                f.write(OSU_TMPL.format(diff="Local", bid=-1, sid=-1))
            with open(p, "rb") as f:
                md5 = hashlib.md5(f.read()).hexdigest()
            raw = {"beatmaps": [{"onlineId": -1, "md5": md5, "stars": 6.5}],
                   "scores": [], "sets": {}}
            realm = normalize_realm_dump(raw)
            b = parse_lazer_blob(p, realm)
            self.assertAlmostEqual(b.stars, 6.5)

    def test_onlineid_still_works(self):
        raw = {"beatmaps": [{"onlineId": 77, "md5": MD5_X, "stars": 3.5}],
               "scores": [], "sets": {}}
        r = normalize_realm_dump(raw)
        self.assertAlmostEqual(r["stars"][77], 3.5)
        self.assertAlmostEqual(r["stars"]["77"], 3.5)
        self.assertAlmostEqual(r["stars"][MD5_X], 3.5)


if __name__ == "__main__":
    unittest.main()
