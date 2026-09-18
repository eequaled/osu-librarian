import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.server as servermod


class ExpiryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump({"mode": "stable", "api": {
                "client_id": 0, "client_secret": "",
                "redirect_uri": "http://localhost:8787/api/auth/callback",
                "user_id": 0}}, f)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_expired_without_refresh_returns_empty(self):
        tok = {"access_token": "stale-bearer", "expires_in": 3600,
               "obtained_at": time.time() - 7200}
        with open(".token.json", "w", encoding="utf-8") as f:
            json.dump(tok, f)
        self.assertEqual(servermod.valid_token(servermod.load_settings()), "")

    def test_fresh_without_refresh_still_works(self):
        tok = {"access_token": "fresh-bearer", "expires_in": 3600,
               "obtained_at": time.time()}
        with open(".token.json", "w", encoding="utf-8") as f:
            json.dump(tok, f)
        self.assertEqual(servermod.valid_token(servermod.load_settings()),
                         "fresh-bearer")

    def test_corrupt_values_do_not_raise_and_return_empty(self):
        for bad in ({"access_token": "x", "obtained_at": "junk",
                     "expires_in": "junk"},
                    {"access_token": "x", "obtained_at": None,
                     "expires_in": None},
                    {"access_token": "x", "obtained_at": [1],
                     "expires_in": {}},
                    {"access_token": "x"}):
            with open(".token.json", "w", encoding="utf-8") as f:
                json.dump(bad, f)
            try:
                out = servermod.valid_token(servermod.load_settings())
            except Exception as e:
                self.fail(f"raised {e} for {bad}")
            self.assertEqual(out, "", bad)


if __name__ == "__main__":
    unittest.main()
