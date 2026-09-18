import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.server as servermod


class RefreshIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump({"mode": "stable", "api": {
                "client_id": 1, "client_secret": "s",
                "redirect_uri": "http://localhost:8787/api/auth/callback",
                "user_id": 0}}, f)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_refresh_carries_identity_when_get_me_fails(self):
        tok = {"access_token": "old", "refresh_token": "r",
               "expires_in": 3600, "obtained_at": time.time() - 7200,
               "user_id": 42, "username": "kept"}
        with open(".token.json", "w", encoding="utf-8") as f:
            json.dump(tok, f)
        new_tok = {"access_token": "new-acc", "expires_in": 3600}
        with mock.patch("src.osu_api.refresh_token", return_value=dict(new_tok)):
            with mock.patch("src.osu_api.get_me", side_effect=Exception("down")):
                out = servermod.valid_token(servermod.load_settings())
        self.assertEqual(out, "new-acc")
        with open(".token.json", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("user_id"), 42)
        self.assertEqual(saved.get("username"), "kept")
        self.assertEqual(saved.get("refresh_token"), "r")

    def test_refresh_overwrites_identity_on_get_me_success(self):
        tok = {"access_token": "old", "refresh_token": "r",
               "expires_in": 3600, "obtained_at": time.time() - 7200,
               "user_id": 42, "username": "kept"}
        with open(".token.json", "w", encoding="utf-8") as f:
            json.dump(tok, f)
        new_tok = {"access_token": "new-acc", "expires_in": 3600}
        with mock.patch("src.osu_api.refresh_token", return_value=dict(new_tok)):
            with mock.patch("src.osu_api.get_me",
                            return_value={"id": 7, "username": "fresh"}):
                out = servermod.valid_token(servermod.load_settings())
        self.assertEqual(out, "new-acc")
        with open(".token.json", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("user_id"), 7)
        self.assertEqual(saved.get("username"), "fresh")


if __name__ == "__main__":
    unittest.main()
