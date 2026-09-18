"""Local-app side of one-click linking via the hosted relay (relay/ untouched).

Covers GET /api/auth/relay-return (fake relay over loopback, no real
network), GET /api/auth/status relay block, and the authorize URL format.
"""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.server as servermod  # noqa: E402
from src.server import Handler  # noqa: E402

FAKE_TICKET = "abcdef0123456789" * 4
FAKE_TOKEN = {
    "access_token": "SECRET_RELAY_ACCESS_1",
    "refresh_token": "SECRET_RELAY_REFRESH_1",
    "expires_in": 86400,
    "token_type": "Bearer",
}
EVIL_USER = "<img src=x onerror=alert(1)>"
CLIENT_SECRET = "shhh-test-secret"


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


class _FakeRelayHandler(BaseHTTPRequestHandler):
    ticket = FAKE_TICKET
    consumed = False

    def log_message(self, fmt, *args):  # quiet
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/pair":
            type(self).consumed = False
            return self._json(200, {"ticket": type(self).ticket,
                                   "expires_in": 300})
        return self._json(404, {"error": "not found"})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/token":
            qs = urllib.parse.parse_qs(parsed.query or "")
            ticket = (qs.get("ticket", [""])[0] or "")
            if ticket != type(self).ticket or type(self).consumed:
                return self._json(404, {"error": "bad or used ticket"})
            type(self).consumed = True
            return self._json(200, dict(FAKE_TOKEN))
        return self._json(404, {"error": "not found"})


def _build_relay_authorize_url(relay_url, relay_client_id, ticket, port):
    """Mirror of web/auth.js buildRelayAuthorizeUrl (frontend-built)."""
    redirect = relay_url.rstrip("/") + "/auth/callback"
    return ("https://osu.ppy.sh/oauth/authorize"
            f"?client_id={urllib.parse.quote(str(relay_client_id), safe='')}"
            f"&redirect_uri={urllib.parse.quote(redirect, safe='')}"
            "&response_type=code"
            "&scope=identify+public"
            f"&state={urllib.parse.quote(f'{ticket}.{port}', safe='')}")


class RelayFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        # Fake relay first so settings can point at it.
        cls.relay = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRelayHandler)
        cls.relay_port = cls.relay.server_address[1]
        cls.relay_thread = threading.Thread(target=cls.relay.serve_forever,
                                            daemon=True)
        cls.relay_thread.start()
        cls.relay_url = f"http://127.0.0.1:{cls.relay_port}"
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(cls._settings(), f)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.relay.shutdown()
        cls.relay.server_close()
        os.chdir(cls.cwd)
        cls.tmp.cleanup()

    @classmethod
    def _settings(cls, relay=True):
        api = {
            "client_id": 12345,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": "http://localhost:8787/api/auth/callback",
            "user_id": 0,
        }
        if relay:
            api["relay_url"] = cls.relay_url
            api["relay_client_id"] = 99999
        else:
            api["relay_url"] = ""
            api["relay_client_id"] = 0
        return {
            "mode": "stable",
            "stable_dir": "/tmp/fake-stable",
            "songs_dir": "/tmp/fake-stable/Songs",
            "osu_db": "",
            "scores_db": "",
            "lazer_dir": "",
            "realm_export": "",
            "api": api,
        }

    def setUp(self):
        servermod._current_mode = None
        _FakeRelayHandler.consumed = False
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(self._settings(relay=True), f)
        try:
            os.remove(".token.json")
        except OSError:
            pass

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_relay_return_happy_path_saves_token_0600(self):
        with mock.patch("src.osu_api.get_me",
                        return_value={"id": 4242, "username": EVIL_USER}):
            code, headers, body = _req(
                "GET", self.url(f"/api/auth/relay-return?ticket={FAKE_TICKET}"))
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8", "replace")
        self.assertIn("Account linked as", text)
        self.assertIn("Close this tab", text)
        # Escaped identity, never raw HTML or secret material.
        self.assertNotIn("<img", text)
        self.assertIn("&lt;img", text)
        for secret in ("SECRET_RELAY_ACCESS_1", "SECRET_RELAY_REFRESH_1",
                       CLIENT_SECRET, "access_token", "refresh_token",
                       "client_secret"):
            self.assertNotIn(secret, text)
        self.assertTrue(os.path.exists(".token.json"))
        with open(".token.json", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("access_token"), "SECRET_RELAY_ACCESS_1")
        self.assertEqual(saved.get("user_id"), 4242)
        self.assertEqual(saved.get("username"), EVIL_USER)
        self.assertIn("obtained_at", saved)
        if os.name == "posix":
            mode = stat.S_IMODE(os.stat(".token.json").st_mode)
            self.assertEqual(mode, 0o600)

    def test_relay_return_token_single_use(self):
        with mock.patch("src.osu_api.get_me",
                        return_value={"id": 7, "username": "relay-user"}):
            code, _, _ = _req(
                "GET", self.url(f"/api/auth/relay-return?ticket={FAKE_TICKET}"))
        self.assertEqual(code, 200)
        # Same ticket again: relay 404s -> local 400, no secrets leaked.
        with mock.patch("src.osu_api.get_me",
                        return_value={"id": 7, "username": "relay-user"}):
            code, headers, body = _req(
                "GET", self.url(f"/api/auth/relay-return?ticket={FAKE_TICKET}"))
        self.assertEqual(code, 400)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8", "replace")
        self.assertNotIn("SECRET_RELAY_ACCESS_1", text)
        self.assertNotIn("SECRET_RELAY_REFRESH_1", text)
        self.assertNotIn(CLIENT_SECRET, text)

    def test_relay_return_bad_ticket_400(self):
        for bad in ("", "not-hex!!!", "xyz", "a" * 129,
                    "abcdef0123456789;DROP"):
            code, headers, body = _req(
                "GET", self.url(f"/api/auth/relay-return?ticket={bad}"))
            self.assertEqual(code, 400, bad)
            self.assertIn("text/html", headers.get("Content-Type", ""))
            text = body.decode("utf-8", "replace")
            self.assertNotIn("SECRET_RELAY_ACCESS_1", text)
            self.assertNotIn(CLIENT_SECRET, text)
        # Well-formed hex but unknown to the relay -> 400 as well.
        code, _, body = _req(
            "GET", self.url("/api/auth/relay-return?ticket=deadbeef"))
        self.assertEqual(code, 400)
        self.assertNotIn("SECRET_RELAY_ACCESS_1",
                         body.decode("utf-8", "replace"))
        self.assertFalse(os.path.exists(".token.json"))

    def test_relay_return_no_relay_configured_400(self):
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(self._settings(relay=False), f)
        code, headers, body = _req(
            "GET", self.url(f"/api/auth/relay-return?ticket={FAKE_TICKET}"))
        self.assertEqual(code, 400)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8", "replace")
        self.assertNotIn("SECRET_RELAY_ACCESS_1", text)
        self.assertNotIn(CLIENT_SECRET, text)
        self.assertFalse(os.path.exists(".token.json"))

    def test_status_contains_relay_block_and_keeps_keys(self):
        code, _, body = _req("GET", self.url("/api/auth/status"))
        self.assertEqual(code, 200)
        st = json.loads(body)
        for key in ("linked", "user_id", "username", "redirect_uri",
                    "configured", "relay"):
            self.assertIn(key, st)
        relay = st["relay"]
        self.assertEqual(relay["url"], self.relay_url)
        self.assertEqual(relay["client_id"], 99999)
        self.assertTrue(relay["available"])
        raw = body.decode("utf-8", "replace")
        self.assertNotIn(CLIENT_SECRET, raw)
        self.assertNotIn("SECRET_RELAY", raw)
        # Unconfigured relay -> available False, keys still present.
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(self._settings(relay=False), f)
        code, _, body = _req("GET", self.url("/api/auth/status"))
        st = json.loads(body)
        self.assertIn("relay", st)
        self.assertFalse(st["relay"]["available"])
        self.assertIn("configured", st)

    def test_authorize_url_format_state_passthrough_and_scope(self):
        ticket, port = FAKE_TICKET, str(self.port)
        url = _build_relay_authorize_url(self.relay_url, 99999, ticket, port)
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "osu.ppy.sh")
        self.assertEqual(parsed.path, "/oauth/authorize")
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs.get("client_id"), ["99999"])
        self.assertEqual(qs.get("redirect_uri"),
                         [f"{self.relay_url}/auth/callback"])
        self.assertEqual(qs.get("response_type"), ["code"])
        # scope=identify+public decodes to "identify public".
        self.assertEqual(qs.get("scope"), ["identify public"])
        self.assertIn("scope=identify+public", parsed.query)
        # state passes ticket + local port through untouched.
        self.assertEqual(qs.get("state"), [f"{ticket}.{port}"])
        # Frontend implements this exact format (no new server endpoint).
        repo = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(repo, "web", "auth.js"), encoding="utf-8") as f:
            js = f.read()
        for needle in ("oauth/authorize", "/pair", "scope=identify+public",
                       "state=", "buildRelayAuthorizeUrl", "auth-relay-open"):
            self.assertIn(needle, js)
        with open(os.path.join(repo, "web", "index.html"), encoding="utf-8") as f:
            html_doc = f.read()
        for needle in ("auth-relay-open", "Link with the Librarian app",
                       "auth-advanced", "auth-client-id", "auth-client-secret",
                       "auth-save", "auth-callback", "auth-copy", "auth-open"):
            self.assertIn(needle, html_doc)


if __name__ == "__main__":
    unittest.main()
