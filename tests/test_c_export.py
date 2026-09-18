"""C-tier export edge cases: headers, filenames, ids validation.

Covers POST /api/export header/name gaps without touching
tests/test_server.py (not owned here).
"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server as servermod  # noqa: E402
from src.server import Handler  # noqa: E402
from tests._helpers import req as _req  # noqa: E402
from tests.make_mock_library import make_mock_install  # noqa: E402


class ExportHeadersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        mock = make_mock_install(cls.tmp.name)
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": mock["songs"],
                       "osu_db": "", "scores_db": ""}, f)
        servermod._current_mode = None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()
        # seed the scan cache once
        code, _, body = _req("POST", cls.url_cls("/api/scan"),
                             {"mode": "stable", "fresh": True})
        assert code == 200, body[:200]
        jid = json.loads(body)["job_id"]
        end = time.time() + 20.0
        while time.time() < end:
            c2, _, b2 = _req("GET", cls.url_cls(f"/api/jobs/{jid}"))
            assert c2 == 200
            job = json.loads(b2)
            if job["state"] in ("done", "error"):
                assert job["state"] == "done", job
                break
            time.sleep(0.1)
        else:
            raise AssertionError("scan job timed out")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    @classmethod
    def url_cls(cls, p):
        return f"http://127.0.0.1:{cls.port}{p}"

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def _ids(self, n=2):
        _, _, body = _req("GET", self.url("/api/library"))
        maps = json.loads(body)["maps"]
        return [m["id"] for m in maps[:n]]

    def test_json_headers_name_count_sha(self):
        ids = self._ids(2)
        code, headers, body = _req("POST", self.url("/api/export"),
                                   {"ids": ids, "format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertIn('filename="selection.json"',
                      headers.get("Content-Disposition", ""))
        self.assertEqual(headers.get("X-Export-Count"), str(len(ids)))
        self.assertEqual(headers.get("X-Export-Sha"),
                         hashlib.sha1(body).hexdigest()[:12])
        self.assertEqual(len(json.loads(body)), 2)

    def test_txt_headers(self):
        code, headers, body = _req("POST", self.url("/api/export"),
                                   {"format": "txt"})
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "text/plain")
        self.assertIn('filename="selection.txt"',
                      headers.get("Content-Disposition", ""))
        self.assertTrue(body.decode().strip())

    def test_collection_headers(self):
        code, headers, body = _req("POST", self.url("/api/export"),
                                   {"format": "collection"})
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"),
                         "application/octet-stream")
        self.assertIn('filename="collection.db"',
                      headers.get("Content-Disposition", ""))
        self.assertEqual(headers.get("X-Export-Count"), "3")
        self.assertEqual(headers.get("X-Export-Sha"),
                         hashlib.sha1(body).hexdigest()[:12])

    def test_collection_custom_name_accepted(self):
        code, _, body = _req("POST", self.url("/api/export"),
                             {"format": "collection", "name": "My Set"})
        self.assertEqual(code, 200)
        self.assertTrue(len(body) > 8)

    def test_bogus_format_400(self):
        code, _, body = _req("POST", self.url("/api/export"),
                             {"format": "bogus"})
        self.assertEqual(code, 400)
        self.assertIn(b"format", body)

    def test_bad_ids_400(self):
        for bad in ({"ids": "nope", "format": "json"},
                    {"ids": [123], "format": "json"},
                    {"ids": "abc", "format": "txt"}):
            code, _, _ = _req("POST", self.url("/api/export"), bad)
            self.assertEqual(code, 400, bad)

    def test_empty_ids_empty_export(self):
        code, headers, body = _req("POST", self.url("/api/export"),
                                   {"ids": [], "format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), [])
        self.assertEqual(headers.get("X-Export-Count"), "0")

    def test_missing_ids_exports_all(self):
        code, headers, body = _req("POST", self.url("/api/export"),
                                   {"format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)), 3)
        self.assertEqual(headers.get("X-Export-Count"), "3")


if __name__ == "__main__":
    unittest.main()
