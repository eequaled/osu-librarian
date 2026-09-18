"""B-tier finding 4: secret whitespace is stripped (stdlib unittest)."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import http.client
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import server as relay  # type: ignore
except ImportError:
    from relay import server as relay  # type: ignore


class SecretStripCase(unittest.TestCase):
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

    def test_get_config_strips_secret_direct(self):
        os.environ["OSU_CLIENT_SECRET"] = "   "
        _cid, sec, _url, _port = relay.get_config()
        self.assertEqual(sec, "")
        os.environ["OSU_CLIENT_SECRET"] = "  abc123  "
        _cid2, sec2, _u2, _p2 = relay.get_config()
        self.assertEqual(sec2, "abc123")

    def test_whitespace_secret_fails_closed_at_pair(self):
        os.environ["OSU_CLIENT_SECRET"] = "   "
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/pair", data=b"", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                code = r.status
                body = r.read()
        except urllib.error.HTTPError as e:
            try:
                code = e.code
                body = e.read()
            finally:
                try:
                    e.close()
                except Exception:
                    pass
        self.assertEqual(code, 503)
        self.assertIn("relay", json.loads(body.decode())["error"].lower())

    def test_stripped_secret_sent_to_upstream(self):
        os.environ["OSU_CLIENT_SECRET"] = "  test-client-secret-xyz-123  "
        seen = {}

        def fake_post(url, fields, timeout=15):
            seen.update(dict(fields))
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 10}

        def fake_get(url, token, timeout=15):
            return {"id": 1, "username": "u"}

        relay._post = fake_post  # type: ignore
        relay._get = fake_get  # type: ignore
        # pair then callback to capture secret actually sent
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/pair", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ticket = json.loads(r.read().decode())["ticket"]
        q = urllib.parse.urlencode({"code": "somecode", "state": f"{ticket}.8787"})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", f"/auth/callback?{q}")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 302)
        finally:
            conn.close()
        self.assertEqual(seen.get("client_secret"), "test-client-secret-xyz-123")


if __name__ == "__main__":
    unittest.main()
