import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.server import Handler  # noqa: E402


def _raw(method, url, payload_bytes):
    req = urllib.request.Request(url, data=payload_bytes, headers={}, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class NonDictBodyTests(unittest.TestCase):
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

    def test_list_body_does_not_500(self):
        code, _, _ = _raw("POST", self.url("/api/mode"), b"[1,2,3]")
        self.assertIn(code, (200, 400))
        self.assertNotEqual(code, 500)

    def test_string_body_does_not_500(self):
        code, _, _ = _raw("POST", self.url("/api/mode"), b'"hi"')
        self.assertEqual(code, 400)

    def test_number_body_does_not_500(self):
        code, _, _ = _raw("POST", self.url("/api/scan"), b"123")
        self.assertIn(code, (200, 400, 409))
        self.assertNotEqual(code, 500)

    def test_null_body_treated_as_empty(self):
        code, _, _ = _raw("POST", self.url("/api/mode"), b"null")
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
