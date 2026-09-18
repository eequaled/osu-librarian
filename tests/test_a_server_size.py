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


def _raw(method, url, payload_bytes):
    req = urllib.request.Request(url, data=payload_bytes, headers={}, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class BodySizeTests(unittest.TestCase):
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

    def test_oversized_body_returns_413(self):
        # Patch the cap small so the test stays fast and reliable;
        # the same Content-Length path triggers 413 for real 2MB bodies.
        big = json.dumps({"mode": "stable", "pad": "x" * 2048}).encode()
        with mock.patch("src.server.MAX_BODY_BYTES", 1024):
            code, _, _ = _raw("POST", self.url("/api/scan"), big)
        self.assertEqual(code, 413)

    def test_real_cap_rejects_huge_body(self):
        big = json.dumps({"mode": "stable", "pad": "x" * (2 * 1024 * 1024 + 100)}).encode()
        for _ in range(3):
            try:
                code, _, _ = _raw("POST", self.url("/api/scan"), big)
            except urllib.error.URLError as e:
                # Server closed early while client was still sending;
                # that still means the huge body was rejected.
                if "Broken pipe" in str(e) or "Connection reset" in str(e):
                    return
                raise
            if code == 413:
                return
        self.assertEqual(code, 413)

    def test_small_body_still_works(self):
        small = json.dumps({"mode": "bogus"}).encode()
        code, _, _ = _raw("POST", self.url("/api/scan"), small)
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
