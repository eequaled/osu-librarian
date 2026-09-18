import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.server import Handler  # noqa: E402
from tests.make_mock_library import make_mock_install  # noqa: E402


def _req(method, url, body=None, raw=None, headers=None):
    if raw is not None:
        data = raw
    elif body is not None:
        data = json.dumps(body).encode()
    else:
        data = None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    if body is not None and raw is None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class ExportIdsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        mock = make_mock_install(cls.tmp.name)
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": mock["songs"],
                       "osu_db": "", "scores_db": ""}, f)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        # fresh scan so library has 3 rows
        code, _, body = _req("POST", cls._url("/api/scan"),
                             {"mode": "stable", "fresh": True})
        jid = json.loads(body)["job_id"]
        end = time.time() + 20
        while time.time() < end:
            c2, _, b2 = _req("GET", cls._url(f"/api/jobs/{jid}"))
            if json.loads(b2)["state"] in ("done", "error"):
                break
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    @classmethod
    def _url(cls, p):
        return f"http://127.0.0.1:{cls.port}{p}"

    def url(self, p):
        return self._url(p)

    def test_missing_ids_is_whole_library(self):
        code, headers, body = _req("POST", self.url("/api/export"), {"format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)), 3)
        self.assertEqual(headers.get("X-Export-Count"), "3")

    def test_none_ids_is_whole_library(self):
        code, _, body = _req("POST", self.url("/api/export"),
                             {"ids": None, "format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)), 3)

    def test_empty_list_is_empty_export(self):
        code, headers, body = _req("POST", self.url("/api/export"),
                                   {"ids": [], "format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), [])
        self.assertEqual(headers.get("X-Export-Count"), "0")

    def test_invalid_ids_rejected(self):
        for bad in ("abc", 123, {"a": 1}, [1, 2], [None], [{"id": 1}]):
            code, _, _ = _req("POST", self.url("/api/export"),
                              {"ids": bad, "format": "json"})
            self.assertEqual(code, 400, bad)


if __name__ == "__main__":
    unittest.main()
