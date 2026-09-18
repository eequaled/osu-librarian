import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.server import Handler  # noqa: E402


def _req(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


GOOD = {"id": "good1", "beatmap_id": 1, "mode": 0, "artist": "a",
        "title": "t", "creator": "c", "diff": "d"}
BAD_ROWS = ["oops", 123, None, {"noid": 1}, {"id": ""}, GOOD]


class CorruptRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": "/tmp/nope",
                       "osu_db": "", "scores_db": ""}, f)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_status_survives_bad_rows(self):
        with mock.patch("src.server.cachemod.load_scan",
                         return_value=(["k"], list(BAD_ROWS), {"v": 1})):
            code, _, body = _req("GET", self.url("/api/status"))
        self.assertEqual(code, 200)
        json.loads(body)

    def test_library_filters_bad_rows(self):
        with mock.patch("src.server.cachemod.load_scan",
                         return_value=(["k"], list(BAD_ROWS), {"v": 1})):
            code, _, body = _req("GET", self.url("/api/library"))
        self.assertEqual(code, 200)
        lib = json.loads(body)
        self.assertEqual(len(lib["maps"]), 1)
        self.assertEqual(lib["maps"][0]["id"], "good1")

    def test_export_survives_bad_rows(self):
        with mock.patch("src.server.cachemod.load_scan",
                         return_value=(["k"], list(BAD_ROWS), {"v": 1})):
            code, headers, body = _req("POST", self.url("/api/export"),
                                       {"format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)), 1)
        self.assertEqual(headers.get("X-Export-Count"), "1")


if __name__ == "__main__":
    unittest.main()
