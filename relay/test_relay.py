"""Relay tests (stdlib unittest, no external network).

Localhost HTTP against an ephemeral relay server is used; osu! itself is
simulated by monkeypatching relay._post / relay._get. Run:

    python3 -m unittest discover -s relay
"""

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
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # `discover -s relay` imports this file as top-level `test_relay`
    import server as relay  # type: ignore
except ImportError:  # `discover` from repo root / direct `relay.test_relay`
    from relay import server as relay  # type: ignore

FAKE_ID = "424242"
FAKE_SECRET = "test-client-secret-xyz-123"
FAKE_PUBLIC = "https://link.example.com"
FAKE_CODE = "code-UNDER-TEST-abc-999"


class RelayCase(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in
                         ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL", "PORT")}
        os.environ["OSU_CLIENT_ID"] = FAKE_ID
        os.environ["OSU_CLIENT_SECRET"] = FAKE_SECRET
        os.environ["RELAY_PUBLIC_URL"] = FAKE_PUBLIC
        relay._reset_state()
        self._orig_post = relay._post
        self._orig_get = relay._get
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._stop_server)
        self.addCleanup(self._restore_patches)

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

    def _restore_patches(self):
        relay._post = self._orig_post
        relay._get = self._orig_get
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- tiny HTTP helpers (localhost only, no external network) --

    def _base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _post_pair(self):
        req = urllib.request.Request(self._base() + "/pair", data=b"",
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            finally:
                try:
                    e.close()
                except Exception:
                    pass

    def _get_json(self, path: str):
        req = urllib.request.Request(self._base() + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, dict(e.headers), e.read()
            finally:
                try:
                    e.close()
                except Exception:
                    pass

    def _get_raw(self, path: str):
        """Raw GET without following redirects (for asserting 302 Location)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    # -- tests --

    def test_health(self):
        code, _headers, body = self._get_json("/health")
        self.assertEqual(code, 200)

    def test_browser_cors_for_pair(self):
        """The localhost app calls /pair cross-origin: preflight + ACAO needed."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("OPTIONS", "/pair", headers={"Origin": "http://localhost:8787"})
            resp = conn.getresponse()
            headers = dict(resp.getheaders())
            resp.read()
        finally:
            conn.close()
        self.assertEqual(resp.status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        req = urllib.request.Request(self._base() + "/pair", data=b"", method="POST",
                                     headers={"Origin": "http://localhost:8787"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "*")
            self.assertIn("ticket", json.loads(r.read().decode()))
        self.assertEqual(json.loads(body.decode()), {"ok": True})

    def test_pair_callback_token_happy_path_one_time(self):
        code, pair = self._post_pair()
        self.assertEqual(code, 200)
        ticket = pair["ticket"]
        self.assertEqual(pair["expires_in"], 600)
        self.assertEqual(len(ticket), 64)
        int(ticket, 16)  # must be hex

        seen_posts: list[dict] = []

        def fake_post(url, fields, timeout=15):
            seen_posts.append({"url": url, "fields": dict(fields)})
            self.assertEqual(url, "https://osu.ppy.sh/oauth/token")
            self.assertEqual(fields["grant_type"], "authorization_code")
            self.assertEqual(fields["client_id"], FAKE_ID)
            self.assertEqual(fields["client_secret"], FAKE_SECRET)
            self.assertEqual(fields["code"], FAKE_CODE)
            self.assertEqual(fields["redirect_uri"], FAKE_PUBLIC + "/auth/callback")
            return {"access_token": "AT-123", "refresh_token": "RT-456",
                    "expires_in": 86400}

        def fake_get(url, token, timeout=15):
            self.assertEqual(url, "https://osu.ppy.sh/api/v2/me/")
            self.assertEqual(token, "AT-123")
            return {"id": 76543, "username": "TestUser"}

        relay._post = fake_post  # type: ignore
        relay._get = fake_get  # type: ignore

        local_port = 8787
        state = f"{ticket}.{local_port}"
        q = urllib.parse.urlencode({"code": FAKE_CODE, "state": state})
        status, headers, _body = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(status, 302)
        loc = headers.get("Location", "")
        self.assertEqual(
            loc, f"http://127.0.0.1:{local_port}/api/auth/relay-return?ticket={ticket}")

        code, _h, body = self._get_json(f"/token?ticket={ticket}")
        self.assertEqual(code, 200)
        tok = json.loads(body.decode())
        self.assertEqual(tok["access_token"], "AT-123")
        self.assertEqual(tok["refresh_token"], "RT-456")
        self.assertEqual(tok["expires_in"], 86400)
        self.assertEqual(tok["user_id"], 76543)
        self.assertEqual(tok["username"], "TestUser")
        self.assertIsInstance(tok["obtained_at"], int)

        # One-time: second fetch must 404.
        code2, _h2, body2 = self._get_json(f"/token?ticket={ticket}")
        self.assertEqual(code2, 404)
        self.assertIn("error", json.loads(body2.decode()))

        # Client secret must never appear in relay responses.
        self.assertNotIn(FAKE_SECRET, body.decode())
        self.assertNotIn(FAKE_SECRET, loc)

    def test_bad_state_and_port(self):
        code, pair = self._post_pair()
        self.assertEqual(code, 200)
        good = pair["ticket"]

        def fake_post(url, fields, timeout=15):  # pragma: no cover
            self.fail("exchange must not be attempted for bad state")

        def fake_get(url, token, timeout=15):  # pragma: no cover
            self.fail("me fetch must not be attempted for bad state")

        relay._post = fake_post  # type: ignore
        relay._get = fake_get  # type: ignore

        bad_states = [
            "",  # empty
            "nothex.8787",  # non-hex ticket
            "abc.8787",  # too short
            "z" * 64 + ".8787",  # non-hex chars
            f"{good}.0",  # port 0
            f"{good}.65536",  # port too big
            f"{good}.abc",  # non-numeric port
            f"{good}.",  # empty port
            f"{good}",  # no dot at all
            f"{good}.8787.extra",  # extra dot -> bad port
            "0" * 64 + ".8787",  # well-formed but unknown ticket
        ]
        for bad in bad_states:
            with self.subTest(state=bad):
                q = urllib.parse.urlencode({"code": "somecode", "state": bad})
                status, headers, body = self._get_raw(f"/auth/callback?{q}")
                self.assertEqual(status, 400)
                ctype = headers.get("Content-Type", "")
                self.assertIn("text/html", ctype)
                text = body.decode()
                self.assertNotIn("somecode", text)

        # Missing code / missing state are also 400 HTML.
        for path in (f"/auth/callback?state={good}.8787",
                     f"/auth/callback?code=somecode"):
            status, _h, body = self._get_raw(path)
            self.assertEqual(status, 400)
            self.assertIn("text/html", _h.get("Content-Type", ""))

    def test_expired_ticket(self):
        _code, pair = self._post_pair()
        ticket = pair["ticket"]
        # Inject an old timestamp to simulate expiry (TTL 600s).
        with relay._lock:
            relay._tickets[ticket]["created_at"] = time.time() - 700

        relay._post = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore
            AssertionError("must not call osu! for expired ticket"))
        relay._get = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore
            AssertionError("must not call osu! for expired ticket"))

        q = urllib.parse.urlencode({"code": "c", "state": f"{ticket}.8787"})
        status, _h, _body = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(status, 400)

        code, _h2, body2 = self._get_json(f"/token?ticket={ticket}")
        self.assertEqual(code, 404)
        self.assertIn("error", json.loads(body2.decode()))

    def test_exchange_failure_hides_code_and_secret(self):
        _code, pair = self._post_pair()
        ticket = pair["ticket"]
        evil_code = "EVIL-CODE-" + "C" * 16

        def fake_post(url, fields, timeout=15):
            self.assertEqual(fields["code"], evil_code)
            raise relay.RelayError("token exchange failed: HTTP 400: invalid_grant")

        relay._post = fake_post  # type: ignore
        relay._get = lambda *a, **k: {"id": 1}  # type: ignore

        q = urllib.parse.urlencode({"code": evil_code, "state": f"{ticket}.8787"})
        status, headers, body = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(status, 400)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode()
        self.assertIn("invalid_grant", text)  # escaped reason is shown
        self.assertNotIn(evil_code, text)  # code never echoed
        self.assertNotIn(FAKE_SECRET, text)  # secret never echoed
        # & < > escaping sanity: reason with markup must be escaped.
        relay._post = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore
            relay.RelayError("token exchange failed: <b>oops</b> & done"))

        q2 = urllib.parse.urlencode({"code": "other", "state": f"{ticket}.8787"})
        _s2, _h2, body_b = self._get_raw(f"/auth/callback?{q2}")
        text_b = body_b.decode()
        self.assertNotIn("<b>oops</b>", text_b)
        self.assertIn("&lt;b&gt;oops&lt;/b&gt;", text_b)

    def test_rate_limit_trips(self):
        last = None
        for i in range(20):
            code, _pair = self._post_pair()
            self.assertEqual(code, 200, f"request {i + 1} should pass")
        code, body = self._post_pair()
        last = (code, body)
        self.assertEqual(last[0], 429)
        self.assertIn("error", last[1])
        # Stays limited.
        code2, _b2 = self._post_pair()
        self.assertEqual(code2, 429)

    def test_token_unknown_and_pending_are_404(self):
        # Unknown ticket.
        code, _h, body = self._get_json("/token?ticket=" + "a" * 64)
        self.assertEqual(code, 404)
        self.assertIn("error", json.loads(body.decode()))
        # Paired but not yet exchanged -> same 404 shape (no oracle).
        _c, pair = self._post_pair()
        code2, _h2, body2 = self._get_json(f"/token?ticket={pair['ticket']}")
        self.assertEqual(code2, 404)
        self.assertIn("error", json.loads(body2.decode()))
        # Malformed ticket param.
        code3, _h3, body3 = self._get_json("/token?ticket=abc")
        self.assertEqual(code3, 404)

    def test_no_secrets_leak_into_logs_or_bodies(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _c, pair = self._post_pair()
            ticket = pair["ticket"]
            relay._post = lambda *a, **k: {"access_token": "AT",  # type: ignore
                                           "refresh_token": "RT", "expires_in": 1}
            relay._get = lambda *a, **k: {"id": 9, "username": "u"}  # type: ignore
            q = urllib.parse.urlencode({"code": FAKE_CODE, "state": f"{ticket}.9999"})
            self._get_raw(f"/auth/callback?{q}")
            self._get_json(f"/token?ticket={ticket}")
            self._get_json("/health")
            self._get_json("/token?ticket=" + "b" * 64)
        logs = buf.getvalue()
        self.assertNotIn(FAKE_SECRET, logs)
        self.assertNotIn(FAKE_CODE, logs)
        self.assertNotIn(ticket, logs)  # query strings (tickets) never logged

    def test_me_failure_still_stores_token(self):
        # Decision: /me failure is tolerated (token still relayed, id 0/empty).
        _c, pair = self._post_pair()
        ticket = pair["ticket"]
        relay._post = lambda *a, **k: {"access_token": "AT-X",  # type: ignore
                                       "refresh_token": "", "expires_in": 100}
        relay._get = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore
            relay.RelayError("profile fetch failed: upstream unreachable"))
        q = urllib.parse.urlencode({"code": "cc", "state": f"{ticket}.8787"})
        status, headers, _b = self._get_raw(f"/auth/callback?{q}")
        self.assertEqual(status, 302)
        code, _h, body = self._get_json(f"/token?ticket={ticket}")
        self.assertEqual(code, 200)
        tok = json.loads(body.decode())
        self.assertEqual(tok["access_token"], "AT-X")
        self.assertEqual(tok["user_id"], 0)


if __name__ == "__main__":
    unittest.main()
