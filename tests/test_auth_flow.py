import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.server as servermod  # noqa: E402
from src.server import Handler  # noqa: E402


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


BASE_SETTINGS = {
    "mode": "stable",
    "stable_dir": "/tmp/fake-stable",
    "songs_dir": "/tmp/fake-stable/Songs",
    "osu_db": "",
    "scores_db": "",
    "lazer_dir": "",
    "realm_export": "",
    "api": {
        "client_id": 12345,
        "client_secret": "shhh-test-secret",
        "redirect_uri": "http://localhost:8787/api/auth/callback",
        "user_id": 0,
    },
}


class AuthFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(BASE_SETTINGS, f)
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

    def setUp(self):
        servermod._current_mode = None
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(json.loads(json.dumps(BASE_SETTINGS)), f)
        try:
            os.remove(".token.json")
        except OSError:
            pass

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_callback_error_param_returns_400_html(self):
        code, headers, body = _req(
            "GET", self.url("/api/auth/callback?error=access_denied"))
        self.assertEqual(code, 400)
        ctype = headers.get("Content-Type", "")
        self.assertIn("text/html", ctype)
        text = body.decode("utf-8", "replace")
        self.assertTrue("failed" in text.lower() or "denied" in text.lower())
        self.assertIn("access_denied", text)

    def test_callback_missing_code_returns_400_html(self):
        code, headers, body = _req("GET", self.url("/api/auth/callback"))
        self.assertEqual(code, 400)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("missing code", body.decode("utf-8", "replace").lower())

    def test_callback_success_escapes_username_writes_token_no_secrets(self):
        evil = "<img src=x onerror=alert(1)>"
        fake_tok = {
            "access_token": "SECRET_ACCESS_123",
            "refresh_token": "SECRET_REFRESH_456",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        with mock.patch("src.osu_api.exchange_code", return_value=dict(fake_tok)):
            with mock.patch("src.osu_api.get_me",
                            return_value={"id": 999, "username": evil}):
                code, headers, body = _req(
                    "GET", self.url("/api/auth/callback?code=abc123"))
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8", "replace")
        self.assertIn("Account linked as", text)
        self.assertIn("You can close this tab and return to osu! Librarian.", text)
        # escaped, not raw
        self.assertNotIn("<img", text)
        self.assertIn("&lt;img", text)
        # no secret material in response body
        self.assertNotIn("SECRET_ACCESS_123", text)
        self.assertNotIn("SECRET_REFRESH_456", text)
        self.assertNotIn("shhh-test-secret", text)
        self.assertNotIn("access_token", text)
        self.assertNotIn("refresh_token", text)
        self.assertNotIn("client_secret", text)
        # token persisted
        self.assertTrue(os.path.exists(".token.json"))
        with open(".token.json", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("access_token"), "SECRET_ACCESS_123")
        self.assertEqual(saved.get("user_id"), 999)
        self.assertEqual(saved.get("username"), evil)
        # 0o600 perms (best-effort on posix)
        if os.name == "posix":
            mode = stat.S_IMODE(os.stat(".token.json").st_mode)
            self.assertEqual(mode, 0o600)
        else:
            self.assertTrue(os.path.exists(".token.json"))

    def test_callback_exchange_failure_returns_400_html(self):
        with mock.patch("src.osu_api.exchange_code",
                        side_effect=Exception("bad code")):
            code, headers, body = _req(
                "GET", self.url("/api/auth/callback?code=badcode"))
        self.assertEqual(code, 400)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8", "replace")
        self.assertIn("exchange failed", text.lower())
        self.assertNotIn("shhh-test-secret", text)

    def test_auth_code_compat_still_works_no_secrets(self):
        fake_tok = {
            "access_token": "CODE_ACCESS_999",
            "refresh_token": "CODE_REFRESH_999",
            "expires_in": 3600,
        }
        with mock.patch("src.osu_api.exchange_code", return_value=dict(fake_tok)):
            with mock.patch("src.osu_api.get_me",
                            return_value={"id": 42, "username": "someone"}):
                code, headers, body = _req(
                    "POST", self.url("/api/auth/code"), {"code": "xyz"})
        self.assertEqual(code, 200)
        payload = json.loads(body)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("user_id"), 42)
        raw = body.decode("utf-8", "replace")
        self.assertNotIn("CODE_ACCESS_999", raw)
        self.assertNotIn("CODE_REFRESH_999", raw)
        self.assertNotIn("shhh-test-secret", raw)

    def test_config_persists_creds_and_preserves_mode_paths(self):
        before = {
            "mode": "stable",
            "stable_dir": "/tmp/keep-stable",
            "songs_dir": "/tmp/keep-stable/Songs",
            "osu_db": "/tmp/keep-stable/osu!.db",
            "scores_db": "",
            "lazer_dir": "/tmp/keep-lazer",
            "realm_export": "",
            "api": {
                "client_id": 0,
                "client_secret": "",
                "redirect_uri": "http://localhost:8787/api/auth/callback",
                "user_id": 0,
            },
        }
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(before, f)
        code, _, body = _req("POST", self.url("/api/auth/config"),
                             {"client_id": 777, "client_secret": "new-secret-xyz"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body).get("ok"))
        with open("settings.json", encoding="utf-8") as f:
            after = json.load(f)
        self.assertEqual(after["api"]["client_id"], 777)
        self.assertEqual(after["api"]["client_secret"], "new-secret-xyz")
        # everything else preserved
        self.assertEqual(after["mode"], "stable")
        self.assertEqual(after["stable_dir"], "/tmp/keep-stable")
        self.assertEqual(after["songs_dir"], "/tmp/keep-stable/Songs")
        self.assertEqual(after["osu_db"], "/tmp/keep-stable/osu!.db")
        self.assertEqual(after["lazer_dir"], "/tmp/keep-lazer")
        self.assertEqual(after["api"]["redirect_uri"],
                         "http://localhost:8787/api/auth/callback")
        # status now configured
        code, _, body = _req("GET", self.url("/api/auth/status"))
        st = json.loads(body)
        self.assertTrue(st["configured"])
        self.assertIn("linked", st)
        self.assertIn("user_id", st)
        raw = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        if isinstance(body, bytes):
            raw = body.decode("utf-8", "replace")
        self.assertNotIn("new-secret-xyz", raw)

    def test_config_validation(self):
        for bad in ({"client_id": 0, "client_secret": "x"},
                    {"client_id": -5, "client_secret": "x"},
                    {"client_id": "abc", "client_secret": "x"},
                    {"client_id": 123, "client_secret": ""},
                    {"client_id": 123}):
            code, _, _ = _req("POST", self.url("/api/auth/config"), bad)
            self.assertEqual(code, 400, bad)

    def test_status_includes_configured_flag(self):
        # unconfigured
        with open("settings.json", "w", encoding="utf-8") as f:
            cfg = json.loads(json.dumps(BASE_SETTINGS))
            cfg["api"]["client_id"] = 0
            cfg["api"]["client_secret"] = ""
            json.dump(cfg, f)
        code, _, body = _req("GET", self.url("/api/auth/status"))
        self.assertEqual(code, 200)
        st = json.loads(body)
        self.assertIn("linked", st)
        self.assertIn("user_id", st)
        self.assertIn("configured", st)
        self.assertFalse(st["configured"])
        # configured
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump(BASE_SETTINGS, f)
        code, _, body = _req("GET", self.url("/api/auth/status"))
        st = json.loads(body)
        self.assertTrue(st["configured"])
        self.assertNotIn("shhh-test-secret", body.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
