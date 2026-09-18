"""C-tier regression tests: provider oauth_error value is never reflected.

Stdlib unittest only, no external network. Run:

    python3 -m unittest discover -s relay
"""

from __future__ import annotations

import http.client
import os
import sys
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # `discover -s relay` imports this file as top-level `test_c_oauth_error`
    import server as relay  # type: ignore
except ImportError:  # `discover` from repo root / direct `relay.test_c_oauth_error`
    from relay import server as relay  # type: ignore


class OAuthErrorCase(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in
                         ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL", "PORT")}
        os.environ["OSU_CLIENT_ID"] = "424242"
        os.environ["OSU_CLIENT_SECRET"] = "test-client-secret-xyz-123"
        os.environ["RELAY_PUBLIC_URL"] = "https://link.example.com"
        relay._reset_state()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
        relay._reset_state()
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get_raw(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def test_oauth_error_value_never_reflected(self):
        evil = ("access_denied-phish: please re-login at "
                "https://evil.example/signin <script>alert(1)</script>")
        q = urllib.parse.urlencode({"error": evil, "state": "x"})
        status, headers, body = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(status, 400)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8", "replace")
        self.assertNotIn(evil, text)
        self.assertNotIn("evil.example", text)
        self.assertNotIn("alert(1)", text)
        self.assertNotIn("<script>", text)
        self.assertIn("Authorization failed", text)

    def test_plain_oauth_error_is_static_too(self):
        q = urllib.parse.urlencode({"error": "access_denied"})
        status, _headers, body = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(status, 400)
        text = body.decode("utf-8", "replace")
        self.assertNotIn("access_denied", text)
        self.assertIn("Authorization failed", text)


if __name__ == "__main__":
    unittest.main()
