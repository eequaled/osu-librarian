"""C-tier parser edge: out-of-range Mode -> unknown, filter must not crash."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.library import Beatmap, Filters, apply_filters  # noqa: E402
from src.osu_parser import mode_name  # noqa: E402


class ModeUnknownTests(unittest.TestCase):
    def test_out_of_range_is_unknown(self):
        for m in (-1, 4, 99, 255):
            self.assertEqual(mode_name(m), "unknown", m)
        for m, want in ((0, "osu"), (1, "taiko"), (2, "catch"), (3, "mania")):
            self.assertEqual(mode_name(m), want)

    def test_mode_filter_does_not_crash(self):
        maps = [
            Beatmap(id="u", set_id="1", mode=99, mode_name="unknown"),
            Beatmap(id="o", set_id="1", mode=0, mode_name="osu"),
        ]
        out = apply_filters(maps, Filters(mode="osu"))
        self.assertEqual([m.id for m in out], ["o"])
        out = apply_filters(maps, Filters(mode="all"))
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
