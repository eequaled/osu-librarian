"""Finding 1: stable scan must not re-read osu!.db + scores.db via _db_only_rows."""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402
from tests.make_mock_library import make_mock_install  # noqa: E402


def _fake_dbmap(**kw):
    d = dict(artist="A", title="T", creator="C", difficulty="D", md5="ab" * 16,
             osu_file="diff.osu", folder="999 Artist - Title", ranked=1, mode=0,
             ar=5.0, cs=4.0, hp=5.0, od=5.0, stars=3.0, total_ms=60000,
             beatmap_id=4242, grades=[0, 0, 0, 0], unplayed=True, last_played=0,
             source="", tags="")
    d.update(kw)
    return SimpleNamespace(**d)


class DbOnlyRowsPreloadedTests(unittest.TestCase):
    def test_preloaded_inputs_skip_disk_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            songs = os.path.join(tmp, "Songs")
            os.makedirs(songs)
            paths = {"songs_dir": songs, "osu_db": os.path.join(tmp, "osu!.db"),
                     "scores_db": os.path.join(tmp, "scores.db")}
            dbmaps = [_fake_dbmap()]
            with mock.patch("src.stable_db.read_osu_db",
                            side_effect=AssertionError("must not re-read osu!.db")), \
                 mock.patch("src.stable_scanner.load_score_map",
                            side_effect=AssertionError("must not re-read scores.db")):
                rows = server._db_only_rows(paths, dbmaps, {})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].beatmap_id, 4242)
            self.assertIn("missing .osu", rows[0].folder)

    def test_fallback_still_reads_when_not_preloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            songs = os.path.join(tmp, "Songs")
            os.makedirs(songs)
            paths = {"songs_dir": songs, "osu_db": os.path.join(tmp, "nope.db"),
                     "scores_db": os.path.join(tmp, "nope-scores.db")}
            with mock.patch("src.stable_db.read_osu_db",
                            side_effect=OSError("missing")) as rdb, \
                 mock.patch("src.stable_scanner.load_score_map",
                            return_value={}) as rscores:
                rows = server._db_only_rows(paths)
            self.assertEqual(rows, [])
            rdb.assert_called_once()
            rscores.assert_not_called()  # early return on unreadable osu!.db

    def test_scan_threads_loaded_maps_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                mock_inst = make_mock_install(tmp)
                paths = {"songs_dir": mock_inst["songs"], "osu_db": "",
                         "scores_db": ""}
                seen = {}
                orig = server._db_only_rows

                def _spy(p, dbmaps=None, score_map=None):
                    seen["dbmaps"] = dbmaps
                    seen["score_map"] = score_map
                    return orig(p, dbmaps, score_map)

                job = SimpleNamespace(total=0, done=0)
                with mock.patch.object(server, "_db_only_rows", side_effect=_spy) as spy, \
                     mock.patch("src.stable_db.read_osu_db",
                                side_effect=AssertionError("second osu!.db read")):
                    server._scan_stable_incremental(job, paths, fresh=True)
                spy.assert_called_once()
                self.assertIsNotNone(seen["dbmaps"])
                self.assertIsNotNone(seen["score_map"])
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
