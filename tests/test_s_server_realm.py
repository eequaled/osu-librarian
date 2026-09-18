import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.server as servermod

OSU = """osu file format v14
[General]
Mode: 0
[Metadata]
Title:T
Artist:A
Creator:C
Version:Insane
BeatmapID:111
BeatmapSetID:1001
[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
[TimingPoints]
0,500,4,2,0,80,1,0
[Events]
0,0,"bg.jpg",0,0
[HitObjects]
256,192,1000,1,0,0:0:0:0:
"""


class FakeJob:
    def __init__(self):
        self.total = 0
        self.done = 0


def _realm_obj(grade):
    return {
        "beatmaps": [{"onlineId": 111, "setOnlineId": 1001,
                      "md5": "a" * 32, "stars": 5.0, "status": 1,
                      "statusName": "ranked", "plays": 2, "grade": grade}],
        "scores": [], "sets": {},
    }


class LazerRealmRejoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def _setup_lazer(self, grade):
        lazer = os.path.join(self.tmp.name, "lazer")
        files_dir = os.path.join(lazer, "files")
        os.makedirs(files_dir, exist_ok=True)
        blob = os.path.join(files_dir, "blob1")
        with open(blob, "w", encoding="utf-8") as f:
            f.write(OSU)
        realm_path = os.path.join(self.tmp.name, "realm.json")
        with open(realm_path, "w", encoding="utf-8") as f:
            json.dump(_realm_obj(grade), f)
        return lazer, files_dir, blob, realm_path

    def test_incremental_rejoins_realm_on_export_change(self):
        lazer, files_dir, blob, realm_path = self._setup_lazer("A")
        paths = {"lazer_dir": lazer, "realm_export": realm_path}
        servermod._scan_lazer_incremental(FakeJob(), paths, True)
        from src import cache as cachemod
        keys, rows, fp = cachemod.load_scan("lazer")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("grade"), "A")
        self.assertIn("realm", fp)

        # change export grade, bump mtime to guarantee detection
        time.sleep(0.02)
        with open(realm_path, "w", encoding="utf-8") as f:
            json.dump(_realm_obj("S"), f)
        os.utime(realm_path, (time.time() + 2, time.time() + 2))

        servermod._scan_lazer_incremental(FakeJob(), paths, False)
        keys2, rows2, fp2 = cachemod.load_scan("lazer")
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0].get("grade"), "S")
        self.assertNotEqual(fp.get("realm"), fp2.get("realm"))

    def test_incremental_reuses_when_realm_unchanged(self):
        lazer, files_dir, blob, realm_path = self._setup_lazer("A")
        paths = {"lazer_dir": lazer, "realm_export": realm_path}
        servermod._scan_lazer_incremental(FakeJob(), paths, True)
        from src import cache as cachemod
        _, rows, fp = cachemod.load_scan("lazer")
        # second incremental with no changes keeps the row
        servermod._scan_lazer_incremental(FakeJob(), paths, False)
        _, rows2, fp2 = cachemod.load_scan("lazer")
        self.assertEqual(rows2[0].get("grade"), "A")
        self.assertEqual(fp.get("realm"), fp2.get("realm"))


if __name__ == "__main__":
    unittest.main()
