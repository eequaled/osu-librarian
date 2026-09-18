import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import load_settings


class CorruptSettingsTests(unittest.TestCase):
    def test_corrupt_json_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "settings.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{not valid json!!!")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                s = load_settings(p)
            self.assertEqual(s.mode, "stable")
            self.assertEqual(s.api.client_id, 0)
            self.assertTrue(err.getvalue().strip())

    def test_bad_ints_fall_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "settings.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"mode": "stable",
                           "api": {"client_id": "abc", "user_id": "xyz",
                                   "relay_client_id": "nope"}}, f)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                s = load_settings(p)
            self.assertEqual(s.api.client_id, 0)
            self.assertEqual(s.api.user_id, 0)
            self.assertEqual(s.api.relay_client_id, 0)

    def test_non_dict_root_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "settings.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump([1, 2, 3], f)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                s = load_settings(p)
            self.assertEqual(s.mode, "stable")


if __name__ == "__main__":
    unittest.main()
