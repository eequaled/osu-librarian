"""Reconciliation endpoint + online_only played filter (spec: unplayed-count).

Covers GET /api/reconciliation math (local/online-only/unplayed buckets,
lazer_style_unplayed, available/linked gates, corrupt rows never 500) and the
tri-state played filter through apply_filters.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server as servermod  # noqa: E402
from src.server import Handler  # noqa: E402
from src.library import Beatmap, Filters, apply_filters  # noqa: E402
from tests._helpers import req as _req  # noqa: E402


def _rows():
    return [
        {"id": "a", "played_local": True, "played_online": False},
        {"id": "b", "played_local": False, "played_online": True},
        {"id": "c", "played_local": False, "played_online": False},
        {"id": "d", "played_local": True, "played_online": True},
        # truthy-but-non-bool flag counts via truthiness (matches the JS
        # isPlayed mirror); only MISSING flags fall through to unplayed.
        {"id": "e", "played_local": "yes", "played_online": None},
    ]


class ReconciliationEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        servermod._current_mode = None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        # seed cache directly through the same atomic writer the scan uses
        rows = _rows()
        servermod._save_scan_atomic("stable",
                                    [r["id"] for r in rows], rows,
                                    {"files": {}})

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    def test_bucket_math(self):
        _, _, body = _req("GET", self._url("/api/reconciliation"))
        j = json.loads(body)
        self.assertTrue(j["available"])
        self.assertEqual(j["total"], 5)
        self.assertEqual(j["local_played"], 3)      # a, d, e (truthy string)
        self.assertEqual(j["online_only"], 1)       # b
        self.assertEqual(j["unplayed"], 1)          # c
        self.assertEqual(j["lazer_style_unplayed"], 2)  # unplayed + online_only
        self.assertIn("linked", j)
        self.assertIsNone(j["checking"])

    def test_never_500(self):
        code, _, body = _req("GET", self._url("/api/reconciliation"))
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body))

    def _url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"


class DuplicatesEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        servermod._current_mode = None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        rows = [
            # same song, two set_ids -> one duplicate group
            {"id": "a1", "set_id": "11", "artist": "Foo", "title": "Bar",
             "diff": "Easy", "mode_name": "osu", "stars": 2.0},
            {"id": "a2", "set_id": "22", "artist": "foo", "title": "BAR",
             "diff": "Insane", "mode_name": "osu", "stars": 5.0},
            # same set_id, different diffs -> NOT a duplicate
            {"id": "b1", "set_id": "33", "artist": "Baz", "title": "Qux",
             "diff": "Normal", "mode_name": "osu"},
            {"id": "b2", "set_id": "33", "artist": "Baz", "title": "Qux",
             "diff": "Hard", "mode_name": "osu"},
            # different song entirely
            {"id": "c1", "set_id": "44", "artist": "Zed", "title": "Solo",
             "diff": "Easy", "mode_name": "taiko"},
        ]
        servermod._save_scan_atomic("stable",
                                    [r["id"] for r in rows], rows,
                                    {"files": {}})

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    def test_grouping(self):
        _, _, body = _req("GET", self._url("/api/duplicates"))
        j = json.loads(body)
        self.assertEqual(j["group_count"], 1)
        g = j["groups"][0]
        self.assertEqual(g["key"], "foo | bar")
        self.assertEqual({r["set_id"] for r in g["rows"]}, {"11", "22"})

    def test_no_500_on_empty(self):
        _, _, body = _req("GET", self._url("/api/duplicates"))
        self.assertEqual(json.loads(body)["group_count"], 1)

    def _url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"


class OnlineOnlyFilterTests(unittest.TestCase):
    def _maps(self):
        return [
            Beatmap(id="a", set_id="1", played_local=True),
            Beatmap(id="b", set_id="1", played_online=True),
            Beatmap(id="c", set_id="1"),
            Beatmap(id="d", set_id="1", played_local=True, played_online=True),
        ]

    def test_online_only_keeps_only_online_rows(self):
        out = apply_filters(self._maps(), Filters(played="online_only"))
        self.assertEqual([m.id for m in out], ["b"])

    def test_played_and_unplayed_unchanged(self):
        self.assertEqual([m.id for m in apply_filters(
            self._maps(), Filters(played="played"))], ["a", "b", "d"])
        self.assertEqual([m.id for m in apply_filters(
            self._maps(), Filters(played="unplayed"))], ["c"])

    def test_all_unknown_value_passes_everything(self):
        self.assertEqual(len(apply_filters(
            self._maps(), Filters(played="bogus"))), 4)


if __name__ == "__main__":
    unittest.main()
