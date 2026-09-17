import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.server import _scan_lazer_incremental, _scan_stable_incremental  # noqa: E402
from tests.make_mock_library import SONG_A  # noqa: E402


class FakeJob:
    def __init__(self):
        self.done = 0
        self.total = 0


class ScanProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def _lazer_paths(self, n_osu=2, n_other=3):
        ldir = os.path.join(self.tmp.name, "lazer")
        blobs = []
        for i in range(n_osu):
            d = os.path.join(ldir, "files", f"{i:02x}")
            os.makedirs(os.path.join(d, f"{i:02x}"), exist_ok=True)
            p = os.path.join(d, f"{i:02x}", "f" * 64)
            with open(p, "w") as f:
                f.write(SONG_A.replace("BeatmapID:111", f"BeatmapID:{200 + i}"))
            blobs.append(p)
        for i in range(n_other):
            d = os.path.join(ldir, "files", "zz")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"noise{i}.mp3"), "w") as f:
                f.write("not a beatmap")
        return {"lazer_dir": ldir, "realm_export": ""}

    def test_lazer_fresh_then_incremental(self):
        from src import cache as cachemod
        paths = self._lazer_paths()
        job = FakeJob()
        _scan_lazer_incremental(job, paths, fresh=True)
        self.assertLessEqual(job.done, job.total)
        keys, rows, _fp = cachemod.load_scan("lazer")
        self.assertEqual(len(rows), 2)  # only .osu blobs, no junk rows
        self.assertTrue(all(k is not None for k in keys))  # real blob keys

        job2 = FakeJob()
        _scan_lazer_incremental(job2, paths, fresh=False)
        self.assertLessEqual(job2.done, job2.total)
        keys2, rows2, _fp2 = cachemod.load_scan("lazer")
        self.assertEqual(len(rows2), 2)
        self.assertEqual([r["id"] for r in rows2], [r["id"] for r in rows])

        # adding one map rescans without overflow or junk
        d = os.path.join(paths["lazer_dir"], "files", "aa", "bb")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "g" * 64), "w") as f:
            f.write(SONG_A.replace("BeatmapID:111", "BeatmapID:299"))
        job3 = FakeJob()
        _scan_lazer_incremental(job3, paths, fresh=False)
        self.assertLessEqual(job3.done, job3.total)
        _, rows3, _ = cachemod.load_scan("lazer")
        self.assertEqual(len(rows3), 3)

    def test_stable_incremental_no_overflow(self):
        from src import cache as cachemod
        from tests.make_mock_library import make_mock_install
        mock = make_mock_install(self.tmp.name)
        paths = {"songs_dir": mock["songs"], "osu_db": "", "scores_db": ""}
        _scan_stable_incremental(FakeJob(), paths, fresh=True)
        job = FakeJob()
        _scan_stable_incremental(job, paths, fresh=False)
        self.assertLessEqual(job.done, job.total)
        _, rows, _ = cachemod.load_scan("stable")
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
