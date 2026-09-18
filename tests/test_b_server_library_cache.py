"""Finding 5: /api/library needs consistent ETag + revalidating cache headers."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class LibraryCacheTests(unittest.TestCase):
    def test_etag_and_revalidation_headers(self):
        import urllib.error  # noqa: F401 (used in _get)
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                rows = [{"id": "a", "set_id": "s1"},
                        {"id": "b", "set_id": "s1"}]
                server._save_scan_atomic("stable", ["a", "b"], rows,
                                         {"files": {}})
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                port = httpd.server_address[1]
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    base = f"http://127.0.0.1:{port}/api/library"
                    code, headers, body = _get(base)
                    self.assertEqual(code, 200)
                    etag = headers.get("ETag")
                    self.assertTrue(etag)
                    self.assertNotIn("no-store",
                                     headers.get("Cache-Control", ""))
                    self.assertIn("no-cache", headers.get("Cache-Control", ""))
                    self.assertIn("private", headers.get("Cache-Control", ""))
                    self.assertEqual(len(json.loads(body)["maps"]), 2)
                    code2, headers2, _ = _get(
                        base, {"If-None-Match": etag})
                    self.assertEqual(code2, 304)
                    self.assertEqual(headers2.get("ETag"), etag)
                    self.assertIn("no-cache",
                                  headers2.get("Cache-Control", ""))
                finally:
                    httpd.shutdown()
                    httpd.server_close()
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
