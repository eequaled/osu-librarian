import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.server import Handler  # noqa: E402

SNIPPET = '<script>try{if(window.opener){window.opener.postMessage({type:"osu-librarian-linked"},location.origin);}}catch(e){}</script>'


def _req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={}, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            e.close()
        except Exception:
            pass
        return e.code, dict(e.headers), data


class RelayMsgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cwd = os.getcwd()
        os.chdir(cls.tmp.name)
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump({"mode": "stable", "api": {
                "client_id": 1, "client_secret": "s",
                "redirect_uri": "http://localhost:8787/api/auth/callback",
                "user_id": 0,
                "relay_url": "http://127.0.0.1:9",
                "relay_client_id": 1}}, f)
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

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_relay_return_failure_has_postmessage(self):
        code, _, body = _req("GET", self.url("/api/auth/relay-return?ticket=ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34"))
        self.assertEqual(code, 400)
        self.assertIn(SNIPPET, body.decode())

    def test_relay_return_success_has_postmessage(self):
        fake_tok = {"access_token": "A", "expires_in": 3600}
        with mock.patch("src.server._fetch_relay_token",
                        return_value=(True, dict(fake_tok))):
            with mock.patch("src.osu_api.get_me",
                            return_value={"id": 1, "username": "u"}):
                code, _, body = _req(
                    "GET", self.url("/api/auth/relay-return?ticket=ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34ab12cd34"))
        self.assertEqual(code, 200)
        self.assertIn(SNIPPET, body.decode())


if __name__ == "__main__":
    unittest.main()
