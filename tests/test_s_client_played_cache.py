"""Client-stream regression: played cache is per-user and legacy entries expire.

Pure-python level, no network (user_has_scores is stubbed).
"""
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import osu_api  # noqa: E402
from src.osu_api import _cache_hit, mark_online_played  # noqa: E402


def _map(bid=123):
    return SimpleNamespace(beatmap_id=bid, played_online=False)


class PlayedCacheIsolationTests(unittest.TestCase):
    def test_legacy_bare_key_not_reused_across_users(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = os.path.join(td, ".api_cache.json")
            # Pre-TTL cache: bare beatmap_id key, as written by older versions.
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"123": {"played": True, "at": time.time()}}, f)
            calls = []
            orig = osu_api.user_has_scores
            osu_api.user_has_scores = lambda bid, uid, tok, ruleset="": calls.append((bid, uid)) or False
            try:
                maps = [_map(123)]
                stats = mark_online_played(maps, 999, "tok", cache_path=cache_path,
                                           sleep_s=0, ttl_days=7.0)
            finally:
                osu_api.user_has_scores = orig
            self.assertFalse(maps[0].played_online)
            self.assertEqual(len(calls), 1)
            self.assertEqual(stats["checked"], 1)
            with open(cache_path, encoding="utf-8") as f:
                cache = json.load(f)
            self.assertIn("999:123", cache)

    def test_bool_entries_expire(self):
        found, _ = _cache_hit({"1:2": True}, "1:2", 7.0)
        self.assertFalse(found)
        found, _ = _cache_hit({"123": True}, "123", 7.0)
        self.assertFalse(found)

    def test_no_cross_account_leakage(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = os.path.join(td, ".api_cache.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"1:123": {"played": True, "at": time.time()}}, f)
            calls = []
            orig = osu_api.user_has_scores
            osu_api.user_has_scores = lambda bid, uid, tok, ruleset="": calls.append((bid, uid)) or False
            try:
                maps = [_map(123)]
                mark_online_played(maps, 2, "tok", cache_path=cache_path,
                                   sleep_s=0, ttl_days=7.0)
            finally:
                osu_api.user_has_scores = orig
            # User 2 must be checked fresh, not served user 1's "played" entry.
            self.assertFalse(maps[0].played_online)
            self.assertEqual(calls, [(123, 2)])


if __name__ == "__main__":
    unittest.main()
