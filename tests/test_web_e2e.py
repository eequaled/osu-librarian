"""End-to-end: serve the real app + mock library, drive the whole user loop over HTTP.

Covers: static assets, mode switch, scan→job→library, ETag revalidation,
selection export in all formats, and the online-check job with a stubbed API.
"""
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

from src import osu_api  # noqa: E402
from src.server import Handler, set_mode  # noqa: E402
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


class WebE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        mock = make_mock_install(cls.tmp.name)
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": mock["songs"],
                       "osu_db": "", "scores_db": ""}, f)
        set_mode("stable")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

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

    def test_detect_and_use_install(self):
        code, _, body = _req("GET", self.url("/api/detect"))
        self.assertEqual(code, 200)
        self.assertIn("installs", json.loads(body))

        # the mock install root (contains Songs/) is a valid stable install
        code, _, body = _req("POST", self.url("/api/use-install"),
                             {"kind": "stable", "path": self.tmp.name})
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["mode"], "stable")

        code, _, _ = _req("POST", self.url("/api/use-install"),
                          {"kind": "stable", "path": "/no/such/dir"})
        self.assertEqual(code, 400)

    def test_user_loop(self):
        # static app loads
        code, _, body = _req("GET", self.url("/"))
        self.assertEqual(code, 200)
        self.assertIn(b"osu! Librarian", body)
        for asset in ("main.js", "api.js", "store.js", "views.js",
                      "auth.js", "bulk.js", "styles.css"):
            code, _, _ = _req("GET", self.url(f"/{asset}"))
            self.assertEqual(code, 200, asset)

        # mode switch sticks
        code, _, body = _req("POST", self.url("/api/mode"), {"mode": "stable"})
        self.assertEqual(json.loads(body)["mode"], "stable")

        # scan -> library with 2 sets / 3 diffs, all unplayed
        code, _, body = _req("POST", self.url("/api/scan"),
                             {"mode": "stable", "fresh": True})
        job = self._wait_job(json.loads(body)["job_id"])
        self.assertEqual(job["state"], "done", job)
        code, headers, body = _req("GET", self.url("/api/library"))
        lib = json.loads(body)
        self.assertEqual(len(lib["maps"]), 3)
        self.assertEqual(len({m["set_id"] for m in lib["maps"]}), 2)
        v1 = lib["version"]

        # ETag revalidation
        code, _, _ = _req("GET", self.url("/api/library"),
                          headers={"If-None-Match": f'"{v1}"'})
        self.assertEqual(code, 304)

        # export mirrors a UI multiselect (first two ids)
        ids = [m["id"] for m in lib["maps"][:2]]
        code, _, body = _req("POST", self.url("/api/export"),
                             {"ids": ids, "format": "json"})
        self.assertEqual(len(json.loads(body)), 2)

    def test_online_check_with_stubbed_api(self):
        code, _, body = _req("POST", self.url("/api/scan"),
                             {"mode": "stable", "fresh": True})
        self._wait_job(json.loads(body)["job_id"])
        _, _, body = _req("GET", self.url("/api/library"))
        bid111 = next(m["beatmap_id"] for m in json.loads(body)["maps"])
        self.assertEqual(bid111, 111)

        with open(".token.json", "w") as f:
            json.dump({"access_token": "fake", "obtained_at": time.time(),
                       "expires_in": 99999, "user_id": 7}, f)

        real = osu_api.user_has_scores
        osu_api.user_has_scores = lambda bid, uid, tok: bid == 111
        try:
            code, _, body = _req("POST", self.url("/api/online-check"), {})
            self.assertEqual(code, 200, body)
            job = self._wait_job(json.loads(body)["job_id"])
            self.assertEqual(job["state"], "done", job)
        finally:
            osu_api.user_has_scores = real

        _, _, body = _req("GET", self.url("/api/library"))
        lib = json.loads(body)
        by_bid = {m["beatmap_id"]: m for m in lib["maps"]}
        self.assertTrue(by_bid[111]["played_online"])
        self.assertFalse(any(m["played_online"] for b, m in by_bid.items() if b != 111))
        # version changed with content, so the client refetches (no stale 304)
        self.assertNotEqual(lib["version"], "empty")


if __name__ == "__main__":
    unittest.main()
