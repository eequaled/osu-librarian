"""B-tier regression: stars cache key has size; access is locked."""
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import difficulty


class CacheKeyTests(unittest.TestCase):
    def setUp(self):
        difficulty.clear_cache()

    def tearDown(self):
        difficulty.clear_cache()

    def test_key_includes_size(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            with open(p, "w") as f:
                f.write("aaa")
            with mock.patch.dict(sys.modules, {"rosu_pp_py": mock.MagicMock()}):
                with mock.patch.object(difficulty, "_compute_uncached",
                                        return_value=1.0) as m:
                    v1 = difficulty.stars_for_file(p, 0)
                    self.assertEqual(v1, 1.0)
                    self.assertEqual(m.call_count, 1)
                keys = list(difficulty._CACHE.keys())
                self.assertEqual(len(keys), 1)
                self.assertEqual(len(keys[0]), 4, "key must be (abspath, mtime_ns, size, mode)")
                abspath, mtime_ns, size, mode = keys[0]
                st = os.stat(p)
                self.assertEqual(size, st.st_size)
                self.assertEqual(mtime_ns, st.st_mtime_ns)
                self.assertEqual(mode, 0)

    def test_same_ns_rewrite_recomputes(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            with open(p, "w") as f:
                f.write("aaa")
            with mock.patch.dict(sys.modules, {"rosu_pp_py": mock.MagicMock()}):
                st0 = os.stat(p)
                with mock.patch.object(difficulty, "_compute_uncached", return_value=1.0):
                    self.assertEqual(difficulty.stars_for_file(p, 0), 1.0)
                # rewrite with different size but force identical mtime_ns
                with open(p, "w") as f:
                    f.write("aaa-bigger-content-here")
                os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns))
                st1 = os.stat(p)
                self.assertEqual(st1.st_mtime_ns, st0.st_mtime_ns)
                self.assertNotEqual(st1.st_size, st0.st_size)
                with mock.patch.object(difficulty, "_compute_uncached",
                                        return_value=2.0) as m:
                    v2 = difficulty.stars_for_file(p, 0)
                    self.assertEqual(v2, 2.0)
                    self.assertEqual(m.call_count, 1, "size change must bust the cache")

    def test_lock_object_exists(self):
        self.assertTrue(hasattr(difficulty, "_CACHE_LOCK"))
        self.assertTrue(difficulty._CACHE_LOCK.acquire(blocking=False))
        difficulty._CACHE_LOCK.release()


class ThreadSafetySmokeTests(unittest.TestCase):
    def setUp(self):
        difficulty.clear_cache()

    def tearDown(self):
        difficulty.clear_cache()

    def test_concurrent_access_no_crash(self):
        import time as _t
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for i in range(4):
                p = os.path.join(td, f"m{i}.osu")
                with open(p, "w") as f:
                    f.write("x" * (10 + i))
                paths.append(p)
            with mock.patch.dict(sys.modules, {"rosu_pp_py": mock.MagicMock()}):
                errors = []

                def slow_compute(path_str, mode_int):
                    _t.sleep(0.005)
                    return 1.5

                with mock.patch.object(difficulty, "_compute_uncached",
                                       side_effect=slow_compute):
                    def worker(n):
                        try:
                            for _ in range(25):
                                difficulty.stars_for_file(paths[n % len(paths)], 0)
                                if n % 5 == 0:
                                    difficulty.clear_cache()
                        except Exception as e:  # noqa: BLE001
                            errors.append(e)

                    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
                    for t in ts:
                        t.start()
                    for t in ts:
                        t.join(timeout=20)
                self.assertFalse([t for t in ts if t.is_alive()], "threads deadlocked")
                self.assertEqual(errors, [])
                self.assertLessEqual(len(difficulty._CACHE), difficulty._CACHE_MAX)


if __name__ == "__main__":
    unittest.main()
