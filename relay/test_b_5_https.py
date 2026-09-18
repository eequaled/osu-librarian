"""B-tier finding 5: HTTPS enforcement with loopback exception (stdlib unittest)."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import server as relay  # type: ignore
except ImportError:
    from relay import server as relay  # type: ignore


class HttpsCase(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL")}
        os.environ["OSU_CLIENT_ID"] = "424242"
        os.environ["OSU_CLIENT_SECRET"] = "test-client-secret-xyz-123"
        os.environ["RELAY_PUBLIC_URL"] = "https://link.example.com"
        relay._reset_state()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        self.port = self.httpd.server_address[1]
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()
        self.addCleanup(self._stop)

    def _stop(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
        relay._reset_state()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _post_pair(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/pair", data=b"", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            finally:
                try:
                    e.close()
                except Exception:
                    pass

    def test_helper_direct(self):
        self.assertTrue(relay._is_public_url_https_ok("https://link.example.com"))
        self.assertTrue(relay._is_public_url_https_ok("https://link.example.com/"))
        self.assertFalse(relay._is_public_url_https_ok("http://example.com"))
        self.assertFalse(relay._is_public_url_https_ok("http://127.0.0.1.evil.com"))
        self.assertFalse(relay._is_public_url_https_ok("http://localhost.evil.com"))
        self.assertFalse(relay._is_public_url_https_ok("example.com"))
        self.assertFalse(relay._is_public_url_https_ok(""))
        # loopback exceptions for tests/local
        self.assertTrue(relay._is_public_url_https_ok("http://127.0.0.1:8099"))
        self.assertTrue(relay._is_public_url_https_ok("http://127.0.0.1"))
        self.assertTrue(relay._is_public_url_https_ok("http://localhost:8099"))
        self.assertTrue(relay._is_public_url_https_ok("http://localhost"))

    def test_pair_503_for_http_public_url(self):
        os.environ["RELAY_PUBLIC_URL"] = "http://example.com"
        code, body = self._post_pair()
        self.assertEqual(code, 503)
        err = str(body.get("error", ""))
        self.assertIn("relay", err.lower())
        self.assertIn("https", err.lower())

    def test_pair_allows_loopback_http(self):
        for url in ("http://127.0.0.1:8099", "http://127.0.0.1",
                    "http://localhost:8099", "http://localhost"):
            with self.subTest(url=url):
                os.environ["RELAY_PUBLIC_URL"] = url
                relay._reset_state()
                code, body = self._post_pair()
                self.assertEqual(code, 200, url)
                self.assertIn("ticket", body)

    def test_pair_allows_https(self):
        os.environ["RELAY_PUBLIC_URL"] = "https://link.example.com"
        code, body = self._post_pair()
        self.assertEqual(code, 200)

    def test_boot_rejects_http_with_stderr(self):
        os.environ["RELAY_PUBLIC_URL"] = "http://example.com"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = relay.main()
        self.assertNotEqual(rc, 0)
        self.assertIn("https", buf.getvalue().lower())
        buf2 = io.StringIO()
        with redirect_stderr(buf2):
            with self.assertRaises(SystemExit) as cm:
                relay.serve(port=0)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("https", buf2.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
