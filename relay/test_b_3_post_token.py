"""B-tier finding 3: POST /token with GET compat (stdlib unittest)."""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import unittest
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


class PostTokenCase(unittest.TestCase):
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

    def _base(self):
        return f"http://127.0.0.1:{self.port}"

    def _pair(self):
        req = urllib.request.Request(self._base() + "/pair", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())["ticket"]

    def _exchange(self, ticket, code="CODE-123"):
        relay._post = lambda *a, **k: {"access_token": "AT-1",  # type: ignore
                                       "refresh_token": "RT-1", "expires_in": 100}
        relay._get = lambda *a, **k: {"id": 7, "username": "u"}  # type: ignore
        q = urllib.parse.urlencode({"code": code, "state": f"{ticket}.8787"})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", f"/auth/callback?{q}")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 302)
        finally:
            conn.close()

    def _post_token(self, body, ctype):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", "/token", body=body,
                         headers={"Content-Type": ctype,
                                  "Content-Length": str(len(body))})
            resp = conn.getresponse()
            headers = dict(resp.getheaders())
            return resp.status, headers, resp.read()
        finally:
            conn.close()

    def _get_token(self, ticket):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", f"/token?ticket={ticket}")
            resp = conn.getresponse()
            headers = dict(resp.getheaders())
            return resp.status, headers, resp.read()
        finally:
            conn.close()

    def test_post_json_same_response_as_get(self):
        t1 = self._pair()
        self._exchange(t1)
        body = json.dumps({"ticket": t1}).encode()
        status, headers, raw = self._post_token(body, "application/json")
        self.assertEqual(status, 200)
        tok = json.loads(raw.decode())
        self.assertEqual(tok["access_token"], "AT-1")
        self.assertEqual(tok["user_id"], 7)
        # one-time: second POST must 404 (gone semantics)
        status2, _, raw2 = self._post_token(body, "application/json")
        self.assertEqual(status2, 404)
        self.assertIn("error", json.loads(raw2.decode()))

    def test_post_form_same_response(self):
        t = self._pair()
        self._exchange(t, code="CODE-FORM")
        form = urllib.parse.urlencode({"ticket": t}).encode()
        status, _, raw = self._post_token(form, "application/x-www-form-urlencoded")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw.decode())["access_token"], "AT-1")

    def test_post_unknown_and_pending_are_404(self):
        # unknown
        body = json.dumps({"ticket": "a" * 64}).encode()
        s, _, raw = self._post_token(body, "application/json")
        self.assertEqual(s, 404)
        # pending (paired but not exchanged)
        t = self._pair()
        body2 = json.dumps({"ticket": t}).encode()
        s2, _, raw2 = self._post_token(body2, "application/json")
        self.assertEqual(s2, 404)
        # malformed
        s3, _, raw3 = self._post_token(json.dumps({"ticket": "abc"}).encode(),
                                       "application/json")
        self.assertEqual(s3, 404)

    def test_get_still_works_backward_compat(self):
        t = self._pair()
        self._exchange(t, code="CODE-GET")
        s, _, raw = self._get_token(t)
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(raw.decode())["access_token"], "AT-1")
        # used -> gone for both verbs
        s2, _, _ = self._get_token(t)
        self.assertEqual(s2, 404)
        t2 = self._pair()
        self._exchange(t2, code="CODE-GET2")
        body = json.dumps({"ticket": t2}).encode()
        s3, _, raw3 = self._post_token(body, "application/json")
        self.assertEqual(s3, 200)
        s4, _, _ = self._get_token(t2)
        self.assertEqual(s4, 404)

    def test_post_token_has_no_cors(self):
        t = self._pair()
        self._exchange(t, code="CODE-CORS")
        body = json.dumps({"ticket": t}).encode()
        _, headers, _ = self._post_token(body, "application/json")
        lowered = {k.lower(): v for k, v in headers.items()}
        self.assertNotIn("access-control-allow-origin", lowered)

    def test_post_token_direct_helper(self):
        self.assertTrue(hasattr(relay.Handler, "_handle_token_post"))
        self.assertTrue(hasattr(relay, "_pop_ticket_payload"))


if __name__ == "__main__":
    unittest.main()
