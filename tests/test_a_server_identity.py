import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.server as servermod  # noqa: E402
from src.server import Handler  # noqa: E402


def _req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={}, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class PersistIdentityTests(unittest.TestCase):
    def test_retry_then_success_saves_identity(self):
        tok = {"access_token": "A", "expires_in": 3600}
        calls = []

        def fake_get_me(_t):
            calls.append(1)
            if len(calls) == 1:
                raise Exception("flaky")
            return {"id": 5, "username": "retry-user"}

        with mock.patch("src.osu_api.get_me", side_effect=fake_get_me):
            with mock.patch("src.server._save_token") as saver:
                ok, payload = servermod._persist_token(dict(tok))
        self.assertTrue(ok)
        self.assertEqual(payload.get("user_id"), 5)
        self.assertEqual(len(calls), 2)
        self.assertTrue(saver.called)

    def test_still_saves_warns_and_flags_when_identity_missing(self):
        tok = {"access_token": "B", "expires_in": 3600}
        with mock.patch("src.osu_api.get_me", side_effect=Exception("down")):
            with mock.patch("src.server._save_token") as saver:
                buf = io.StringIO()
                with redirect_stderr(buf):
                    ok, payload = servermod._persist_token(dict(tok))
        self.assertTrue(ok)
        self.assertTrue(saver.called)
        self.assertTrue(payload.get("linked_without_identity"))
        self.assertIn("without identity", buf.getvalue().lower())

    def test_relay_return_still_links_without_identity(self):
        tmp = tempfile.TemporaryDirectory()
        cwd = os.getcwd()
        os.chdir(tmp.name)
        try:
            with open("settings.json", "w") as f:
                json.dump({"mode": "stable", "api": {
                    "client_id": 1, "client_secret": "s",
                    "redirect_uri": "http://localhost:8787/api/auth/callback",
                    "user_id": 0, "relay_url": "http://127.0.0.1:9",
                    "relay_client_id": 1}}, f)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = httpd.server_address[1]
            th = threading.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            try:
                fake_tok = {"access_token": "C", "expires_in": 3600}
                with mock.patch("src.server._fetch_relay_token",
                                return_value=(True, dict(fake_tok))):
                    with mock.patch("src.osu_api.get_me",
                                    side_effect=Exception("down")):
                        buf = io.StringIO()
                        with redirect_stderr(buf):
                            req = urllib.request.Request(
                                f"http://127.0.0.1:{port}/api/auth/relay-return?ticket=abc123")
                            try:
                                with urllib.request.urlopen(req, timeout=10) as r:
                                    code, body = r.status, r.read()
                            except urllib.error.HTTPError as e:
                                code, body = e.code, e.read()
                self.assertEqual(code, 200)
                self.assertIn("Account linked", body.decode())
                self.assertTrue(os.path.exists(".token.json"))
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            os.chdir(cwd)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
