import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import cache  # noqa: E402
from src.jobs import JobRegistry  # noqa: E402
from src.osu_api import _cache_hit  # noqa: E402
from tests.make_mock_library import make_mock_install  # noqa: E402


class CacheTests(unittest.TestCase):
    def test_diff_fingerprints(self):
        old = {"files": {"a.osu": [1, 10], "b.osu": [2, 20]}, "db": [5, 5]}
        new = {"files": {"a.osu": [1, 10], "b.osu": [3, 20], "c.osu": [4, 1]}, "db": [5, 5]}
        unchanged, changed, deleted, dbs = cache.diff_fingerprints(old, new)
        self.assertEqual(unchanged, {"a.osu"})
        self.assertEqual(changed, {"b.osu", "c.osu"})
        self.assertEqual(deleted, set())
        self.assertFalse(dbs)

    def test_diff_detects_db_change_and_deletes(self):
        old = {"files": {"a.osu": [1, 10]}, "db": [5, 5], "scores": [1, 1]}
        new = {"files": {}, "db": [6, 5], "scores": [1, 1]}
        _, _, deleted, dbs = cache.diff_fingerprints(old, new)
        self.assertEqual(deleted, {"a.osu"})
        self.assertTrue(dbs)

    def test_fingerprint_stable_sees_new_file(self):
        with tempfile.TemporaryDirectory() as td:
            mock = make_mock_install(td)
            fp1 = cache.fingerprint_stable(mock["songs"], "", "")
            self.assertEqual(len(fp1["files"]), 3)
            with open(os.path.join(mock["songs"],
                                   "1002 Mapper Two - Other Track",
                                   "extra.osu"), "w") as f:
                f.write("osu file format v14\n")
            fp2 = cache.fingerprint_stable(mock["songs"], "", "")
            _, changed, _, _ = cache.diff_fingerprints(fp1, fp2)
            self.assertEqual(len(changed), 1)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                cache.save_scan("stable", [{"id": "x"}], {"files": {}})
                rows, fp = cache.load_scan("stable")
                self.assertEqual(rows, [{"id": "x"}])
                self.assertEqual(fp, {"files": {}})
            finally:
                os.chdir(cwd)

    def test_version_changes_with_content(self):
        fp = {"files": {"a": [1, 2]}}
        self.assertNotEqual(cache.version_for(3, fp), cache.version_for(4, fp))


class ApiCacheTests(unittest.TestCase):
    def test_plain_bool_counts_as_fresh(self):
        found, played = _cache_hit({"1": True}, "1", 7.0)
        self.assertTrue(found and played)

    def test_ttl_expiry(self):
        fresh_entry = {"played": True, "at": time.time()}
        stale_entry = {"played": True, "at": time.time() - 8 * 86400}
        self.assertTrue(_cache_hit({"1": fresh_entry}, "1", 7.0)[0])
        self.assertFalse(_cache_hit({"1": stale_entry}, "1", 7.0)[0])
        self.assertFalse(_cache_hit({}, "1", 7.0)[0])


class JobTests(unittest.TestCase):
    def test_background_success_and_error(self):
        reg = JobRegistry()
        job = reg.create("scan", total=2)
        done_evt = __import__("threading").Event()

        def fn(j):
            j.done = 2

        reg.run_background(job, fn)
        done_evt.wait(0.1)
        time.sleep(0.2)
        self.assertEqual(reg.get(job.id).state, "done")

        bad = reg.create("scan")
        reg.run_background(bad, lambda j: 1 / 0)
        time.sleep(0.2)
        self.assertEqual(reg.get(bad.id).state, "error")
        self.assertIn("ZeroDivision", reg.get(bad.id).error)


if __name__ == "__main__":
    unittest.main()
