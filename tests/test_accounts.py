"""Multi-account storage + switch/delete endpoints (spec: product-backlog N10).

Active token lives at TOKEN_PATH (all existing code paths untouched); stored
accounts live in .accounts/*.token.json. Switching is an atomic swap that
keeps 0600 permissions and refuses while jobs run. Each test gets fresh
fixtures (no order dependence).
"""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server as servermod  # noqa: E402
from src.server import ACCOUNTS_DIR, TOKEN_PATH, Handler  # noqa: E402
from tests._helpers import req as _req  # noqa: E402


def _tok(username, user_id):
    return {"access_token": f"tok-{username}", "refresh_token": f"r-{username}",
            "username": username, "user_id": user_id, "obtained_at": 0.0,
            "expires_in": 3600}


class AccountsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self._mode = servermod._current_mode
        servermod._current_mode = None
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # active account alice, stored account bob
        with open(TOKEN_PATH, "w") as f:
            json.dump(_tok("alice", 1), f)
        os.makedirs(ACCOUNTS_DIR, exist_ok=True)
        with open(os.path.join(ACCOUNTS_DIR, "bob.token.json"), "w") as f:
            json.dump(_tok("bob", 2), f)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        os.chdir(self.cwd)
        servermod._current_mode = self._mode
        self.tmp.cleanup()

    def _url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def test_list_accounts(self):
        _, _, body = _req("GET", self._url("/api/accounts"))
        j = json.loads(body)
        names = {a["username"]: a["active"] for a in j["accounts"]}
        self.assertTrue(names["alice"])
        self.assertFalse(names["bob"])

    def test_switch_swaps_files_and_keeps_0600(self):
        code, _, _ = _req("POST", self._url("/api/accounts/switch"),
                          {"username": "bob"})
        self.assertEqual(code, 200)
        active = json.load(open(TOKEN_PATH))
        self.assertEqual(active["username"], "bob")
        alice_path = os.path.join(ACCOUNTS_DIR, "alice.token.json")
        stored = json.load(open(alice_path))
        self.assertEqual(stored["username"], "alice")
        # the server wrote alice's slot: must be 0600
        mode = stat.S_IMODE(os.stat(alice_path).st_mode)
        self.assertEqual(mode, 0o600)
        # bob's old slot is gone (he is active now)
        self.assertFalse(os.path.exists(
            os.path.join(ACCOUNTS_DIR, "bob.token.json")))

    def test_switch_unknown_409(self):
        code, _, body = _req("POST", self._url("/api/accounts/switch"),
                             {"username": "ghost"})
        self.assertEqual(code, 409)
        self.assertIn(b"unknown account", body)

    def test_delete_inactive(self):
        with open(os.path.join(ACCOUNTS_DIR, "carol.token.json"), "w") as f:
            json.dump(_tok("carol", 3), f)
        code, _, _ = _req("DELETE", self._url("/api/accounts/carol"))
        self.assertEqual(code, 200)
        self.assertFalse(os.path.exists(
            os.path.join(ACCOUNTS_DIR, "carol.token.json")))

    def test_delete_active_refused(self):
        code, _, body = _req("DELETE", self._url("/api/accounts/alice"))
        self.assertEqual(code, 404)
        self.assertIn(b"active account", body)


if __name__ == "__main__":
    unittest.main()
