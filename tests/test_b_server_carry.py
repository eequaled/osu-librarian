"""Finding 7: played_online must survive md5 changes via stable beatmap_id."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402


class CarryOnlineTests(unittest.TestCase):
    def test_md5_match_still_wins(self):
        old = [{"id": "md5-a", "beatmap_id": 111, "played_online": True}]
        new = [{"id": "md5-a", "beatmap_id": 111, "played_online": False}]
        server._carry_online(old, new)
        self.assertTrue(new[0]["played_online"])

    def test_edited_map_keeps_flag_by_beatmap_id(self):
        old = [{"id": "md5-old", "beatmap_id": 111, "played_online": True}]
        new = [{"id": "md5-new", "beatmap_id": 111, "played_online": False}]
        server._carry_online(old, new)
        self.assertTrue(new[0]["played_online"])

    def test_unknown_ids_stay_unplayed(self):
        old = [{"id": "md5-old", "beatmap_id": 111, "played_online": True}]
        new = [{"id": "md5-new", "beatmap_id": 222, "played_online": False},
               {"id": "md5-x", "beatmap_id": -1, "played_online": False},
               {"id": "md5-y", "played_online": False}]
        server._carry_online(old, new)
        self.assertFalse(new[0]["played_online"])
        self.assertFalse(new[1]["played_online"])
        self.assertFalse(new[2]["played_online"])

    def test_unplayed_flag_carried_not_invented(self):
        old = [{"id": "md5-old", "beatmap_id": 111, "played_online": False}]
        new = [{"id": "md5-new", "beatmap_id": 111, "played_online": False}]
        server._carry_online(old, new)
        self.assertFalse(new[0]["played_online"])


if __name__ == "__main__":
    unittest.main()
