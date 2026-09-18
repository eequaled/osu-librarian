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


def _header(headers, name):
    lname = name.lower()
    for k, v in headers.items():
        if k.lower() == lname:
            return v
    return None


class CServerFixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        mock = make_mock_install(cls.tmp.name)
        cls.songs = mock["songs"]
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": cls.songs,
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

    def test_scan_starts_with_indeterminate_total(self):
        import src.server as srv
        import src.cache as cachemod
        srv.set_mode("stable")
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": self.songs,
                       "osu_db": "", "scores_db": ""}, f)
        try:
            os.remove(os.path.join(".cache", "scan-stable.json"))
        except OSError:
            pass
        try:
            os.remove(os.path.join(".cache", "manifest.json"))
        except OSError:
            pass
        calls = []
        orig_fp = cachemod.fingerprint
        orig_create = srv.registry.create

        def counting_fp(*a, **k):
            calls.append(1)
            return orig_fp(*a, **k)

        seen = {}

        def cap_create(kind, total=0):
            if kind == "scan" and "total" not in seen:
                seen["total"] = total
            return orig_create(kind, total=total)

        cachemod.fingerprint = counting_fp
        srv.registry.create = cap_create
        try:
            jid = srv.run_scan("stable", True)
        finally:
            cachemod.fingerprint = orig_fp
            srv.registry.create = orig_create
        self.assertTrue(jid)
        # job created indeterminate, not with a pre-walked file count
        self.assertEqual(seen.get("total"), 1)
        # no upfront whole-tree fingerprint; worker fingerprints once
        self.assertEqual(calls, [])
        end = time.time() + 20.0
        job = None
        while time.time() < end:
            job = srv.registry.get(jid)
            if job is not None and job.state in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertIsNotNone(job)
        self.assertEqual(job.state, "done", getattr(job, "error", ""))
        # worker filled in the real total once known
        self.assertGreaterEqual(job.total, 1)
        self.assertEqual(job.done, job.total)

    def test_art_404_when_lazer_dir_empty(self):
        import src.server as srv
        srv.set_mode("lazer")
        with open("settings.json", "w") as f:
            json.dump({"mode": "lazer", "lazer_dir": "", "realm_export": ""}, f)
        srv._art_index["key"] = None
        srv._art_index["by_id"] = {}
        h = "a" * 64
        row = {"id": "row1", "bg_hash": h, "bg": "bg.jpg"}
        srv._save_scan_atomic("lazer", ["k"], [row], {"files": {"k": [1, 2]}})
        # decoy a CWD-relative files/... path the old code would have statted
        decoy = os.path.join("files", h[0], h[:2], h)
        os.makedirs(os.path.dirname(decoy), exist_ok=True)
        with open(decoy, "wb") as f:
            f.write(b"fake-image")
        self.assertTrue(os.path.isfile(decoy))
        code, _, _ = _req("GET", self.url("/api/art?id=row1"))
        self.assertEqual(code, 404)
        # restore stable settings for other tests
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": self.songs,
                       "osu_db": "", "scores_db": ""}, f)
        srv.set_mode("stable")
        srv._art_index["key"] = None
        srv._art_index["by_id"] = {}

    def test_static_etag_304_round_trip(self):
        code, headers, _ = _req("GET", self.url("/"))
        self.assertEqual(code, 200)
        etag = _header(headers, "ETag")
        self.assertTrue(etag)
        code2, headers2, body2 = _req("GET", self.url("/"),
                                      headers={"If-None-Match": etag})
        self.assertEqual(code2, 304)
        self.assertEqual(body2, b"")
        self.assertEqual(_header(headers2, "ETag"), etag)
        code3, _, _ = _req("GET", self.url("/"),
                           headers={"If-None-Match": '"stale"'})
        self.assertEqual(code3, 200)


if __name__ == "__main__":
    unittest.main()
