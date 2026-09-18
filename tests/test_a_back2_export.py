import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.export import to_txt


class TxtStarsTests(unittest.TestCase):
    def test_null_stars_defaults(self):
        rows = [{"artist": "a", "title": "t", "diff": "d",
                 "mode_name": "osu", "stars": None,
                 "played_local": False, "played_online": False,
                 "beatmap_id": 1}]
        out = to_txt(rows).decode("utf-8")
        self.assertIn("0.00", out)

    def test_string_stars(self):
        rows = [{"artist": "a", "title": "t", "diff": "d",
                 "mode_name": "osu", "stars": "4.567",
                 "played_local": False, "played_online": False,
                 "beatmap_id": 2}]
        out = to_txt(rows).decode("utf-8")
        self.assertIn("4.57", out)

    def test_garbage_stars_defaults(self):
        rows = [{"artist": "a", "title": "t", "diff": "d",
                 "mode_name": "osu", "stars": "nope",
                 "played_local": False, "played_online": False,
                 "beatmap_id": 3}]
        out = to_txt(rows).decode("utf-8")
        self.assertIn("0.00", out)


if __name__ == "__main__":
    unittest.main()
