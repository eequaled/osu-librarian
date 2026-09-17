"""Tests for src.difficulty (optional rosu-pp-py star-rating fallback).

Conventions:
- lib-dependent assertions skip gracefully when rosu-pp-py is absent
  (``available() is False``), so this file is green under system python.
- missing-lib behaviour is forced via import hooks (sys.modules=None and
  builtins.__import__ patch) and covers BOTH available() and
  stars_for_file()/fill_rows() in a single run.
- run: python3 -m unittest tests.test_difficulty
"""
import builtins
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import difficulty  # noqa: E402
from tests.make_mock_library import make_mock_install  # noqa: E402

HAS_LIB = difficulty.available()


def _force_missing_sys_modules():
    """Context helper: stash sys.modules entry, set to None. Returns restore fn."""
    had = "rosu_pp_py" in sys.modules
    old = sys.modules.get("rosu_pp_py")
    sys.modules["rosu_pp_py"] = None
    def restore():
        if had:
            sys.modules["rosu_pp_py"] = old
        else:
            sys.modules.pop("rosu_pp_py", None)
    return restore


def _force_missing_import_hook():
    """Patch builtins.__import__ to fail for rosu_pp_py. Returns restore fn."""
    orig = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "rosu_pp_py" or name.startswith("rosu_pp_py."):
            raise ImportError("forced missing rosu_pp_py (test hook)")
        return orig(name, *args, **kwargs)

    builtins.__import__ = fake
    def restore():
        builtins.__import__ = orig
    return restore


class AvailableTests(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(difficulty.available(), bool)

    def test_missing_via_sys_modules(self):
        restore = _force_missing_sys_modules()
        try:
            self.assertFalse(difficulty.available())
        finally:
            restore()

    def test_missing_via_import_hook(self):
        restore = _force_missing_import_hook()
        try:
            self.assertFalse(difficulty.available())
        finally:
            restore()


class StarsForFileTests(unittest.TestCase):
    def setUp(self):
        difficulty.clear_cache()

    def test_missing_file_returns_none(self):
        # Holds with AND without the lib (missing file -> None either way).
        self.assertIsNone(difficulty.stars_for_file("/nonexistent/definitely-missing.osu"))
        self.assertIsNone(difficulty.stars_for_file("/nonexistent/x.osu", mode=3))

    def test_bad_path_types_return_none(self):
        self.assertIsNone(difficulty.stars_for_file(""))
        self.assertIsNone(difficulty.stars_for_file(None))  # type: ignore[arg-type]
        self.assertIsNone(difficulty.stars_for_file(123))  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(difficulty.stars_for_file(td))  # a directory

    def test_invalid_mode_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            p = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            self.assertIsNone(difficulty.stars_for_file(p, mode=99))
            self.assertIsNone(difficulty.stars_for_file(p, mode=-1))
            self.assertIsNone(difficulty.stars_for_file(p, mode="bogus"))  # type: ignore[arg-type]

    @unittest.skipUnless(HAS_LIB, "rosu-pp-py not installed")
    def test_positive_stars_for_real_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            p = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            stars = difficulty.stars_for_file(p)
            self.assertIsNotNone(stars)
            self.assertGreater(stars, 0.0)

    @unittest.skipUnless(HAS_LIB, "rosu-pp-py not installed")
    def test_mode_param_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            p = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            for m in (0, 1, 2, 3):
                v = difficulty.stars_for_file(p, mode=m)
                # Converted modes may legitimately be 0.0, but must not be None/error.
                self.assertIsNotNone(v, f"mode={m}")

    def test_missing_lib_paths_both_hooks_single_run(self):
        """BOTH injection methods x BOTH entry points, in one run."""
        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            p = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            for label, make_hook in (
                ("sys_modules", _force_missing_sys_modules),
                ("import_hook", _force_missing_import_hook),
            ):
                with self.subTest(hook=label):
                    difficulty.clear_cache()
                    restore = make_hook()
                    try:
                        self.assertFalse(difficulty.available())
                        self.assertIsNone(difficulty.stars_for_file(p))
                        self.assertIsNone(difficulty.stars_for_file(p, mode=0))
                        n = difficulty.fill_rows(
                            [{"stars": 0.0, "mode": 0}], lambda _r: p
                        )
                        self.assertEqual(n, 0)
                    finally:
                        restore()
                    # After restoring, availability matches environment again.
                    self.assertEqual(difficulty.available(), HAS_LIB)


class FillRowsTests(unittest.TestCase):
    def setUp(self):
        difficulty.clear_cache()

    @unittest.skipUnless(HAS_LIB, "rosu-pp-py not installed")
    def test_fills_only_falsy_stars_and_returns_count(self):
        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            good = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            rows = [
                {"id": "a", "stars": 0.0, "mode": 0},
                {"id": "b", "stars": 5.0, "mode": 0},  # already rated: untouched
                {"id": "c", "stars": None, "mode": 0},  # falsy: filled
                {"id": "d", "mode": 0},  # missing stars: falsy -> filled
                {"id": "e", "stars": 0.0, "mode": 0},  # unresolvable: untouched
            ]
            calls = []

            def resolve(row):
                calls.append(row["id"])
                if row["id"] == "e":
                    return None
                return good

            n = difficulty.fill_rows(rows, resolve)
            self.assertEqual(n, 3)
            by_id = {r["id"]: r for r in rows}
            self.assertGreater(by_id["a"]["stars"], 0.0)
            self.assertEqual(by_id["b"]["stars"], 5.0)
            self.assertGreater(by_id["c"]["stars"], 0.0)
            self.assertGreater(by_id["d"]["stars"], 0.0)
            self.assertEqual(by_id["e"]["stars"], 0.0)
            # resolve consulted for every falsy row (incl. unresolvable),
            # but NOT for the already-rated row.
            self.assertNotIn("b", calls)
            self.assertEqual(sorted(calls), ["a", "c", "d", "e"])

    def test_leaves_rows_untouched_without_lib(self):
        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            good = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            for label, make_hook in (
                ("sys_modules", _force_missing_sys_modules),
                ("import_hook", _force_missing_import_hook),
            ):
                with self.subTest(hook=label):
                    difficulty.clear_cache()
                    rows = [{"id": "a", "stars": 0.0, "mode": 0}]
                    restore = make_hook()
                    try:
                        n = difficulty.fill_rows(rows, lambda _r: good)
                        self.assertEqual(n, 0)
                        self.assertEqual(rows[0]["stars"], 0.0)
                    finally:
                        restore()

    def test_never_raises_on_bad_rows(self):
        def raising(_row):
            raise RuntimeError("boom")

        # Each of these must not raise; each returns an int.
        self.assertEqual(difficulty.fill_rows(None, lambda _r: None), 0)  # type: ignore[arg-type]
        self.assertEqual(difficulty.fill_rows("nope", lambda _r: None), 0)  # type: ignore[arg-type]
        self.assertEqual(difficulty.fill_rows([], None), 0)  # type: ignore[arg-type]
        self.assertEqual(
            difficulty.fill_rows([None, "x", 42, {"stars": 0.0}], lambda _r: None), 0
        )
        self.assertEqual(
            difficulty.fill_rows([{"stars": 0.0, "mode": 0}], raising), 0
        )
        self.assertEqual(
            difficulty.fill_rows([{"stars": 0.0, "mode": 0}], lambda _r: 12345), 0
        )
        self.assertEqual(
            difficulty.fill_rows(
                [{"stars": 0.0, "mode": 0}], lambda _r: "/nonexistent/missing.osu"
            ),
            0,
        )

    def test_unresolvable_path_returns_zero(self):
        rows = [{"stars": 0.0, "mode": 0}]
        self.assertEqual(difficulty.fill_rows(rows, lambda _r: None), 0)
        self.assertEqual(rows[0]["stars"], 0.0)


class CacheTests(unittest.TestCase):
    def setUp(self):
        difficulty.clear_cache()

    @unittest.skipUnless(HAS_LIB, "rosu-pp-py not installed")
    def test_cache_hit_does_not_recompute(self):
        import rosu_pp_py

        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            p = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            orig_beatmap = rosu_pp_py.Beatmap
            calls = [0]

            def counting_beatmap(*args, **kwargs):
                calls[0] += 1
                return orig_beatmap(*args, **kwargs)

            rosu_pp_py.Beatmap = counting_beatmap  # type: ignore[assignment]
            try:
                difficulty.clear_cache()
                first = difficulty.stars_for_file(p)
                second = difficulty.stars_for_file(p)
                self.assertIsNotNone(first)
                self.assertEqual(first, second)
                self.assertEqual(calls[0], 1, "second call should be an LRU hit")
                # Same via fill_rows: resolve runs each time, calc does not.
                resolve_calls = [0]

                def resolve(_row):
                    resolve_calls[0] += 1
                    return p

                rows = [{"stars": 0.0, "mode": 0}]
                difficulty.fill_rows(rows, resolve)
                rows2 = [{"stars": 0.0, "mode": 0}]
                difficulty.fill_rows(rows2, resolve)
                self.assertEqual(resolve_calls[0], 2)
                self.assertEqual(calls[0], 1, "fill_rows rescans should hit the cache")
                self.assertEqual(rows[0]["stars"], rows2[0]["stars"])
            finally:
                rosu_pp_py.Beatmap = orig_beatmap  # type: ignore[assignment]

    @unittest.skipUnless(HAS_LIB, "rosu-pp-py not installed")
    def test_mtime_change_recomputes(self):
        import rosu_pp_py

        with tempfile.TemporaryDirectory() as td:
            make_mock_install(td)
            p = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
            orig_beatmap = rosu_pp_py.Beatmap
            calls = [0]

            def counting_beatmap(*args, **kwargs):
                calls[0] += 1
                return orig_beatmap(*args, **kwargs)

            rosu_pp_py.Beatmap = counting_beatmap  # type: ignore[assignment]
            try:
                difficulty.clear_cache()
                difficulty.stars_for_file(p)
                self.assertEqual(calls[0], 1)
                # Bump mtime by 2s (ns resolution) -> cache key changes.
                st = os.stat(p)
                os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
                difficulty.stars_for_file(p)
                self.assertEqual(calls[0], 2)
            finally:
                rosu_pp_py.Beatmap = orig_beatmap  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
