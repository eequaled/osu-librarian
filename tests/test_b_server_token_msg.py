"""Finding 8: dead tokens must say to link again; linked reflects validity."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402
from src.config import Settings  # noqa: E402


def _settings_with_creds():
    s = Settings()
    s.api.client_id = 1
    s.api.client_secret = "shh"
    return s


class TokenMessageTests(unittest.TestCase):
    def test_linked_probe(self):
        now = time.time()
        fresh = {"access_token": "a", "obtained_at": now, "expires_in": 3600}
        self.assertTrue(server._token_linked(fresh, Settings()))
        dead = {"access_token": "a", "obtained_at": now - 99999,
                "expires_in": 3600}
        self.assertFalse(server._token_linked(dead, Settings()))
        # expired but refreshable: still linked (split documented on helper)
        refreshable = dict(dead, refresh_token="r")
        self.assertTrue(server._token_linked(refreshable,
                                             _settings_with_creds()))
        self.assertFalse(server._token_linked(refreshable, Settings()))
        self.assertFalse(server._token_linked({}, Settings()))
        self.assertFalse(server._token_linked({"access_token": ""}, Settings()))
        # legacy file without timestamps keeps presence semantics
        self.assertTrue(server._token_linked({"access_token": "a"},
                                             Settings()))

    def test_job_error_points_at_app_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                rows = [{"id": "a", "set_id": "s", "beatmap_id": 1}]
                server._save_scan_atomic("stable", ["a"], rows, {"files": {}})
                with mock.patch.object(server, "valid_token", return_value=""):
                    jid, err = server.run_online_check()
                self.assertEqual(err, "")
                end = time.time() + 10
                while time.time() < end:
                    job = server.registry.get(jid)
                    if job is not None and job.state in ("done", "error"):
                        break
                    time.sleep(0.05)
                job = server.registry.get(jid)
                self.assertEqual(job.state, "error")
                self.assertIn("link the account again", job.error)
                self.assertIn("app dialog", job.error)
                self.assertNotIn("POST /api/auth/code", job.error)
            finally:
                os.chdir(cwd)

    def test_auth_status_linked_follows_validity(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                dead = {"access_token": "a", "obtained_at": time.time() - 99999,
                        "expires_in": 3600, "user_id": 7, "username": "u"}
                with open(".token.json", "w") as f:
                    json.dump(dead, f)
                with open("settings.json", "w") as f:
                    json.dump({"mode": "stable"}, f)
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                port = httpd.server_address[1]
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/auth/status",
                            timeout=10) as r:
                        payload = json.loads(r.read().decode())
                    self.assertFalse(payload["linked"])
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/status",
                            timeout=10) as r:
                        status = json.loads(r.read().decode())
                    self.assertFalse(status["auth"]["linked"])
                finally:
                    httpd.shutdown()
                    httpd.server_close()
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
