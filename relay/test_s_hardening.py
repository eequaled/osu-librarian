"""S-tier hardening regression tests (stdlib unittest, no external network)."""

from __future__ import annotations

import http.client
import io
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import server as relay  # type: ignore
except ImportError:
    from relay import server as relay  # type: ignore

FAKE_ID = "424242"
FAKE_SECRET = "test-client-secret-xyz-123"
FAKE_PUBLIC = "https://link.example.com"


class HardeningCase(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in
                         ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL", "PORT")}
        os.environ["OSU_CLIENT_ID"] = FAKE_ID
        os.environ["OSU_CLIENT_SECRET"] = FAKE_SECRET
        os.environ["RELAY_PUBLIC_URL"] = FAKE_PUBLIC
        relay._reset_state()
        self._orig_post = relay._post
        self._orig_get = relay._get
        self._orig_max_body = relay.MAX_PAIR_BODY
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        # Mirror production timeouts where the harness allows it.
        try:
            self.httpd.daemon_threads = True
        except Exception:
            pass
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._stop_server)
        self.addCleanup(self._restore)

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

    def _restore(self):
        relay._post = self._orig_post
        relay._get = self._orig_get
        relay.MAX_PAIR_BODY = self._orig_max_body
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _base(self):
        return f"http://127.0.0.1:{self.port}"

    def _post_pair_raw(self, body=b"", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", "/pair", body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def _get_raw(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    @staticmethod
    def _has_acao(headers):
        for k, v in headers.items():
            if k.lower() == "access-control-allow-origin":
                return v
        return None

    # -- finding 1: CORS scoping --

    def test_cors_present_on_pair_absent_on_token_and_404(self):
        status, headers, _body = self._post_pair_raw(b"")
        self.assertEqual(status, 200)
        self.assertEqual(self._has_acao(headers), "*")

        # Pending ticket: token endpoint must not be CORS-readable.
        ticket = json.loads(_body.decode())["ticket"]
        s2, h2, _b2 = self._get_raw(f"/token?ticket={ticket}")
        self.assertEqual(s2, 404)
        self.assertIsNone(self._has_acao(h2))

        # Unknown ticket: same, no ACAO.
        s3, h3, _b3 = self._get_raw("/token?ticket=" + "c" * 64)
        self.assertEqual(s3, 404)
        self.assertIsNone(self._has_acao(h3))

        # 404s omit ACAO.
        s4, h4, _b4 = self._get_raw("/nope")
        self.assertEqual(s4, 404)
        self.assertIsNone(self._has_acao(h4))

        # Preflight lives for /pair ...
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("OPTIONS", "/pair")
            resp = conn.getresponse()
            ph = dict(resp.getheaders())
            resp.read()
            self.assertEqual(resp.status, 204)
        finally:
            conn.close()
        self.assertEqual(self._has_acao(ph), "*")

        # ... but not for other paths.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("OPTIONS", "/token")
            resp = conn.getresponse()
            ph2 = dict(resp.getheaders())
            resp.read()
            self.assertEqual(resp.status, 404)
        finally:
            conn.close()
        self.assertIsNone(self._has_acao(ph2))

    # -- finding 2: rate limits --

    def test_token_bucket_429(self):
        for _ in range(20):
            s, _h, _b = self._get_raw("/token?ticket=" + "d" * 64)
            self.assertEqual(s, 404)
        s, _h, body = self._get_raw("/token?ticket=" + "d" * 64)
        self.assertEqual(s, 429)
        self.assertIn("rate_limited", body.decode())
        self.assertIn("application/json", dict(
            (k.lower(), v) for k, v in _h.items()).get("content-type", ""))

    def test_callback_ip_bucket_429_is_json(self):
        for _ in range(20):
            s, _h, _b = self._get_raw("/auth/callback?code=x&state=bad")
            self.assertIn(s, (400, 429))
        s, h, body = self._get_raw("/auth/callback?code=x&state=bad")
        self.assertEqual(s, 429)
        obj = json.loads(body.decode())
        self.assertIn("error", obj)
        # Callback 429 is JSON even though callback errors are HTML.
        ctype = dict((k.lower(), v) for k, v in h.items()).get("content-type", "")
        self.assertIn("application/json", ctype)

    def test_callback_per_ticket_cap_429(self):
        status, _h, body = self._post_pair_raw(b"")
        self.assertEqual(status, 200)
        ticket = json.loads(body.decode())["ticket"]

        def failing_post(url, fields, timeout=15):
            raise relay.RelayError("token exchange failed: HTTP 400: invalid_grant")

        relay._post = failing_post  # type: ignore
        relay._get = lambda *a, **k: {"id": 1}  # type: ignore
        last = None
        for _ in range(10):
            q = urllib.parse.urlencode({"code": "c", "state": f"{ticket}.8787"})
            s, _hh, _bb = self._get_raw(f"/auth/callback?{q}")
            self.assertEqual(s, 400)
            last = s
        self.assertEqual(last, 400)
        q = urllib.parse.urlencode({"code": "c", "state": f"{ticket}.8787"})
        s, _hh, body = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(s, 429)
        self.assertIn("rate_limited", body.decode())

    def test_client_key_is_direct_peer_ip(self):
        class Dummy:
            client_address = ("9.9.9.9", 1234)
            headers = {"X-Forwarded-For": "1.2.3.4"}
        # Must ignore proxy headers for now (later tier adds trusted XFF).
        self.assertEqual(relay._client_key(Dummy()), "9.9.9.9")

    # -- finding 3: second exchange rejected --

    def test_second_exchange_rejected_without_overwrite(self):
        status, _h, body = self._post_pair_raw(b"")
        ticket = json.loads(body.decode())["ticket"]
        calls = []

        def fake_post_first(url, fields, timeout=15):
            calls.append(dict(fields))
            return {"access_token": "AT-FIRST", "refresh_token": "RT",
                    "expires_in": 100}

        relay._post = fake_post_first  # type: ignore
        relay._get = lambda *a, **k: {"id": 7, "username": "u"}  # type: ignore
        q = urllib.parse.urlencode({"code": "first-code", "state": f"{ticket}.8787"})
        s, _hh, _bb = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(s, 302)
        self.assertEqual(len(calls), 1)

        def fake_post_second(url, fields, timeout=15):
            calls.append(dict(fields))
            return {"access_token": "AT-SECOND", "refresh_token": "RT2",
                    "expires_in": 100}

        relay._post = fake_post_second  # type: ignore
        q2 = urllib.parse.urlencode({"code": "second-code", "state": f"{ticket}.8787"})
        s2, _h2, b2 = self._get_raw(f"/auth/callback?{q2}")
        self.assertEqual(s2, 400)
        # New code must not be consumed: upstream never called again.
        self.assertEqual(len(calls), 1)

        # Stored token is still the first one.
        s3, _h3, b3 = self._get_raw(f"/token?ticket={ticket}")
        self.assertEqual(s3, 200)
        tok = json.loads(b3.decode())
        self.assertEqual(tok["access_token"], "AT-FIRST")

    # -- finding 4: timeouts / daemon threads --

    def test_handler_timeout_and_daemon_threads(self):
        self.assertEqual(relay.Handler.timeout, 60)
        # Production serve() must enable daemon request threads.
        import inspect
        src = inspect.getsource(relay.serve)
        self.assertIn("daemon_threads", src)
        self.assertIn("True", src)

    # -- finding 5: oversized body connection safety --

    def test_discard_body_marks_close_on_oversize(self):
        relay.MAX_PAIR_BODY = 10

        class FakeRFile(io.BytesIO):
            pass

        class FakeHandler:
            def __init__(self, n, payload):
                self.headers = {"Content-Length": str(n)}
                # headers.get must work like email.message.Message.get
                class H(dict):
                    def get(self, k, d=None):
                        return super().get(k, d)
                self.headers = H(self.headers)
                self.rfile = FakeRFile(payload)
                self.close_connection = False

        h = FakeHandler(100, b"x" * 100)
        relay.Handler._discard_body(h)
        self.assertTrue(h.close_connection)

        h2 = FakeHandler(5, b"hello")
        relay.Handler._discard_body(h2)
        self.assertFalse(h2.close_connection)
        self.assertEqual(h2.rfile.read(), b"")

    def test_oversized_post_sends_connection_close_and_stays_usable(self):
        relay.MAX_PAIR_BODY = 10
        big = b"y" * 100
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", "/pair", body=big)
            resp = conn.getresponse()
            headers = dict((k.lower(), v) for k, v in resp.getheaders())
            resp.read()
            self.assertEqual(resp.status, 200)
            self.assertEqual(headers.get("connection"), "close")
        finally:
            conn.close()
        # Server still usable on a fresh connection (no desync).
        s2, _h2, b2 = self._post_pair_raw(b"")
        self.assertEqual(s2, 200)
        self.assertIn("ticket", json.loads(b2.decode()))


if __name__ == "__main__":
    unittest.main()
