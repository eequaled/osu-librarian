"""B-tier contract tests: apply_filters semantics mirror web/store.js."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.library import Beatmap, Filters, apply_filters


def _b(**kw):
    base = dict(id="x", set_id="s1", artist="A", title="T", creator="C",
                diff="Hard", source="", tags="", mode=0, mode_name="osu",
                bpm=120.0, stars=3.0, length_ms=60000, beatmap_id=111,
                ranked="ranked", played_local=False, played_online=False,
                grade="")
    base.update(kw)
    return Beatmap(**base)


class ParityContractTests(unittest.TestCase):
    def test_text_case_insensitive_substring(self):
        maps = [_b(artist="Hello World"), _b(artist="Other")]
        out = apply_filters(maps, Filters(text="  hello "))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].artist, "Hello World")

    def test_text_searches_beatmap_id(self):
        maps = [_b(beatmap_id=987654), _b(beatmap_id=111)]
        out = apply_filters(maps, Filters(text="987654"))
        self.assertEqual([m.beatmap_id for m in out], [987654])

    def test_text_empty_matches_all(self):
        maps = [_b(), _b()]
        self.assertEqual(len(apply_filters(maps, Filters(text="   "))), 2)

    def test_played(self):
        maps = [_b(played_local=True), _b(), _b(played_online=True)]
        self.assertEqual(len(apply_filters(maps, Filters(played="played"))), 2)
        self.assertEqual(len(apply_filters(maps, Filters(played="unplayed"))), 1)
        self.assertEqual(len(apply_filters(maps, Filters(played="all"))), 3)

    def test_mode_exact(self):
        maps = [_b(mode_name="osu"), _b(mode_name="taiko", mode=1)]
        out = apply_filters(maps, Filters(mode="taiko"))
        self.assertEqual([m.mode_name for m in out], ["taiko"])
        self.assertEqual(len(apply_filters(maps, Filters(mode="all"))), 2)

    def test_stars_inclusive(self):
        maps = [_b(stars=1.0), _b(stars=5.0), _b(stars=10.0)]
        out = apply_filters(maps, Filters(star_min=1.0, star_max=5.0))
        self.assertEqual(sorted(m.stars for m in out), [1.0, 5.0])

    def test_star_zero_edge(self):
        maps = [_b(stars=0.0)]
        # unknown SR still matches a 0-min filter
        self.assertEqual(len(apply_filters(maps, Filters(star_min=0.0, star_max=99.0))), 1)
        # but not a positive-min filter
        self.assertEqual(len(apply_filters(maps, Filters(star_min=0.5, star_max=99.0))), 0)
        # zero edge rescues even a negative max window with smin<=0
        self.assertEqual(len(apply_filters(maps, Filters(star_min=0.0, star_max=-0.1))), 1)

    def test_ranked_exact(self):
        maps = [_b(ranked="ranked"), _b(ranked="loved")]
        self.assertEqual([m.ranked for m in apply_filters(maps, Filters(ranked="loved"))],
                         ["loved"])
        self.assertEqual(len(apply_filters(maps, Filters(ranked="all"))), 2)

    def test_sort_keys(self):
        maps = [_b(title="B", diff="z"), _b(title="A", diff="z")]
        out = apply_filters(maps, Filters(sort="title"))
        self.assertEqual([m.title for m in out], ["A", "B"])
        maps = [_b(artist="B", title="x"), _b(artist="A", title="y")]
        out = apply_filters(maps, Filters(sort="artist"))
        self.assertEqual([m.artist for m in out], ["A", "B"])
        maps = [_b(bpm=200.0), _b(bpm=100.0)]
        self.assertEqual([m.bpm for m in apply_filters(maps, Filters(sort="bpm"))],
                         [100.0, 200.0])
        maps = [_b(stars=5.0), _b(stars=1.0)]
        self.assertEqual([m.stars for m in apply_filters(maps, Filters(sort="stars"))],
                         [1.0, 5.0])
        # rank: unplayed-first? code sorts by (not played, grade): played first
        maps = [_b(played_local=False, grade="S"), _b(played_local=True, grade="A")]
        out = apply_filters(maps, Filters(sort="rank"))
        self.assertTrue(out[0].played)
        # unknown sort falls back to title
        maps = [_b(title="B"), _b(title="A")]
        out = apply_filters(maps, Filters(sort="bogus"))
        self.assertEqual([m.title for m in out], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
