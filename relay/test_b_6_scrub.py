"""B-tier finding 6: upstream scrub of secret/code (stdlib unittest)."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.parse
import http.client
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import server as relay  # type: ignore
except ImportError:
    from relay import server as relay  # type: ignore

FAKE_ID = "424242"
FAKE_SECRET = "test-client-secret-xyz-123-SECRET"
FAKE_PUBLIC = "https://link.example.com"
FAKE_CODE = "code-UNDER-TEST-abc-999-CODE"


class ScrubCase(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL")}
        os.environ["OSU_CLIENT_ID"] = FAKE_ID
        os.environ["OSU_CLIENT_SECRET"] = FAKE_SECRET
        os.environ["RELAY_PUBLIC_URL"] = FAKE_PUBLIC
        relay._reset_state()
        self._orig_post = relay._post
        self._orig_get = relay._get
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
        relay._post = self._orig_post
        relay._get = self._orig_get
        relay._reset_state()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_scrub_detail_direct(self):
        self.assertTrue(hasattr(relay, "_scrub_detail"))
        out = relay._scrub_detail(f"bad {FAKE_SECRET} end", [FAKE_SECRET])
        self.assertNotIn(FAKE_SECRET, out)
        self.assertIn("[redacted]", out)
        out2 = relay._scrub_detail(f"bad {FAKE_CODE} end", [FAKE_CODE])
        self.assertNotIn(FAKE_CODE, out2)
        # 12-char prefix catches truncated echoes
        prefix = FAKE_SECRET[:12]
        out3 = relay._scrub_detail(f"oops {prefix} tail", [FAKE_SECRET])
        self.assertNotIn(prefix, out3)
        # empty after scrub -> ""
        self.assertEqual(relay._scrub_detail("   ", [FAKE_SECRET]), "")

    def test_extract_detail_scrubs_secret_and_code(self):
        body = json.dumps({"error": f"invalid {FAKE_SECRET} and {FAKE_CODE}"})
        detail = relay._extract_error_detail(body, [FAKE_SECRET, FAKE_CODE])
        self.assertNotIn(FAKE_SECRET, detail)
        self.assertNotIn(FAKE_CODE, detail)
        self.assertNotIn(FAKE_SECRET[:12], detail)
        # plain-text body
        detail2 = relay._extract_error_detail(f"plain {FAKE_SECRET}",
                                              [FAKE_SECRET])
        self.assertNotIn(FAKE_SECRET, detail2)
        # default (no explicit list) still scrubs secret from env
        body3 = json.dumps({"error_description": f"echo {FAKE_SECRET}"})
        detail3 = relay._extract_error_detail(body3)
        self.assertNotIn(FAKE_SECRET, detail3)
        # empty after scrub -> fallback ""
        detail4 = relay._extract_error_detail(
            json.dumps({"error": FAKE_SECRET}), [FAKE_SECRET])
        # "[redacted]" is acceptable, but must never contain the secret
        self.assertNotIn(FAKE_SECRET, detail4)

    def test_post_scrubs_upstream_http_error(self):
        import io as _io
        import urllib.error as _uerr
        secret = FAKE_SECRET
        code = "SCRUB-CODE-1234567890"
        body = json.dumps({"error": f"nope {secret} {code}"}).encode()
        fp = _io.BytesIO(body)
        hdrs = {"Content-Type": "application/json"}
        # HTTPError needs url/code/msg/hdrs/fp
        err = _uerr.HTTPError("https://osu.ppy.sh/oauth/token", 400,
                              "Bad Request", hdrs, fp)
        orig_open = relay.urllib.request.urlopen
        relay.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(err)  # type: ignore
        try:
            with self.assertRaises(relay.RelayError) as cm:
                relay._post("https://osu.ppy.sh/oauth/token",
                            {"grant_type": "authorization_code",
                             "client_id": FAKE_ID,
                             "client_secret": secret,
                             "code": code,
                             "redirect_uri": FAKE_PUBLIC + "/auth/callback"})
        finally:
            relay.urllib.request.urlopen = orig_open  # type: ignore
        msg = str(cm.exception)
        self.assertNotIn(secret, msg)
        self.assertNotIn(code, msg)
        self.assertNotIn(secret[:12], msg)

    def _pair(self):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/pair", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())["ticket"]

    def test_callback_html_never_echoes_secret_or_code(self):
        ticket = self._pair()
        evil_code = FAKE_CODE

        def fake_post(url, fields, timeout=15):
            # Simulate osu! echoing our secret and code in its error body,
            # then going through the real scrub path via _extract_error_detail.
            body = json.dumps(
                {"error": f"bad {fields.get('client_secret')} {fields.get('code')}"})
            detail = relay._extract_error_detail(
                body, [str(fields.get("client_secret", "")),
                       str(fields.get("code", ""))])
            if detail:
                raise relay.RelayError(
                    f"token exchange failed: HTTP 400: {detail}")
            raise relay.RelayError("token exchange failed: HTTP 400")

        relay._post = fake_post  # type: ignore
        relay._get = lambda *a, **k: {"id": 1}  # type: ignore
        q = urllib.parse.urlencode({"code": evil_code, "state": f"{ticket}.8787"})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", f"/auth/callback?{q}")
            resp = conn.getresponse()
            body = resp.read().decode()
            self.assertEqual(resp.status, 400)
        finally:
            conn.close()
        self.assertNotIn(FAKE_SECRET, body)
        self.assertNotIn(evil_code, body)
        self.assertNotIn(FAKE_SECRET[:12], body)
        self.assertNotIn(evil_code[:12], body)


if __name__ == "__main__":
    unittest.main()
