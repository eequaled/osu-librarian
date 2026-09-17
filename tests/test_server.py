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


class ServerTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def _wait_job(self, jid, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            code, _, body = _req("GET", self.url(f"/api/jobs/{jid}"))
            self.assertEqual(code, 200)
            job = json.loads(body)
            if job["state"] in ("done", "error"):
                return job
            time.sleep(0.1)
        self.fail("job timed out")

    def test_full_loop(self):
        code, _, body = _req("POST", self.url("/api/scan"),
                             {"mode": "stable", "fresh": True})
        self.assertEqual(code, 200)
        job = self._wait_job(json.loads(body)["job_id"])
        self.assertEqual(job["state"], "done", job)

        code, _, body = _req("GET", self.url("/api/status"))
        self.assertEqual(code, 200)
        status = json.loads(body)
        self.assertTrue(status["songs_ok"])
        self.assertTrue(status["scan"]["cached"])
        counts = status["counts"]
        self.assertEqual(counts["diffs"], 3)

        code, headers, body = _req("GET", self.url("/api/library"))
        self.assertEqual(code, 200)
        etag = headers.get("ETag")
        self.assertTrue(etag)
        lib = json.loads(body)
        self.assertEqual(len(lib["maps"]), 3)

        code, _, _ = _req("GET", self.url("/api/library"),
                          headers={"If-None-Match": etag})
        self.assertEqual(code, 304)

        # second scan is incremental and keeps the same rows
        code, _, body = _req("POST", self.url("/api/scan"), {"mode": "stable"})
        job = self._wait_job(json.loads(body)["job_id"])
        self.assertEqual(job["state"], "done", job)
        code, _, body = _req("GET", self.url("/api/library"))
        self.assertEqual(len(json.loads(body)["maps"]), 3)

    def test_export_formats(self):
        code, _, body = _req("POST", self.url("/api/scan"), {"mode": "stable", "fresh": True})
        self._wait_job(json.loads(body)["job_id"])
        code, _, body = _req("GET", self.url("/api/library"))
        ids = [m["id"] for m in json.loads(body)["maps"][:2]]

        code, headers, body = _req("POST", self.url("/api/export"),
                                   {"ids": ids, "format": "json"})
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)), 2)

        code, _, body = _req("POST", self.url("/api/export"), {"format": "txt"})
        self.assertEqual(code, 200)
        self.assertEqual(len(body.decode().strip().splitlines()), 3)

        code, _, body = _req("POST", self.url("/api/export"),
                             {"format": "collection"})
        self.assertEqual(code, 200)
        # version int + collection count int + content
        import struct
        ver, ncol = struct.unpack("<II", body[:8])
        self.assertEqual((ver, ncol), (20150203, 1))

        code, _, _ = _req("POST", self.url("/api/export"), {"format": "bogus"})
        self.assertEqual(code, 400)

    def test_errors(self):
        code, _, _ = _req("GET", self.url("/api/jobs/nope"))
        self.assertEqual(code, 404)
        code, _, _ = _req("POST", self.url("/api/scan"), {"mode": "bogus"})
        self.assertEqual(code, 400)
        code, _, body = _req("POST", self.url("/api/online-check"), {})
        self.assertEqual(code, 400)  # no token linked in test env


if __name__ == "__main__":
    unittest.main()
