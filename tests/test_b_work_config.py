"""B-tier regression: wine default globs users instead of literal $USER."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import config as cfgmod
from src.config import Settings


class WineGlobTests(unittest.TestCase):
    def test_no_literal_dollar_user(self):
        with open(os.path.join(os.path.dirname(__file__), "..",
                               "src", "config.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("$USER", src)

    def test_glob_pattern_follows_detect(self):
        seen_patterns = []
        real_glob = cfgmod.glob.glob

        def fake_glob(pat):
            seen_patterns.append(pat)
            return []

        with mock.patch.object(cfgmod.sys, "platform", "linux"):
            with mock.patch.object(cfgmod.glob, "glob", side_effect=fake_glob):
                with mock.patch.object(cfgmod.os.path, "isdir", return_value=False):
                    cfgmod.default_stable_dir()
        joined = "\n".join(seen_patterns)
        self.assertIn(os.path.join("drive_c", "users"), joined)
        self.assertIn("AppData", joined)
        # wildcard user segment, never a literal name
        self.assertTrue(any(os.path.join("users", "*") in p or "users/*" in p
                            for p in seen_patterns),
                        f"patterns: {seen_patterns}")

    def test_returns_first_glob_hit(self):
        hit = os.path.join("/fake", ".wine", "drive_c", "users", "alice",
                           "AppData", "Local", "osu!")

        def fake_glob(pat):
            if "AppData" in pat:
                return [hit]
            return []

        def fake_isdir(p):
            return p == hit

        with mock.patch.object(cfgmod.sys, "platform", "linux"):
            with mock.patch.object(cfgmod.glob, "glob", side_effect=fake_glob):
                with mock.patch.object(cfgmod.os.path, "isdir", side_effect=fake_isdir):
                    # local-share miss so we fall through to wine glob
                    self.assertEqual(cfgmod.default_stable_dir(), hit)

    def test_explicit_user_config_untouched(self):
        with mock.patch.object(cfgmod, "default_stable_dir", return_value="/glob-hit"):
            with mock.patch.object(cfgmod, "default_lazer_dir", return_value="/lz"):
                s = Settings(stable_dir="/explicit/stable").resolved()
        self.assertEqual(s.stable_dir, "/explicit/stable")


if __name__ == "__main__":
    unittest.main()
