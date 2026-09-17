import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.library import Beatmap, Filters, apply_filters, group_mapsets  # noqa: E402
from src.osu_parser import parse_osu_file  # noqa: E402
from src.stable_scanner import scan_stable  # noqa: E402
from tests.make_mock_library import make_mock_install  # noqa: E402


def test_osu_parser():
    with tempfile.TemporaryDirectory() as td:
        make_mock_install(td)
        p = os.path.join(td, "Songs", "1001 Mapper One - Test Song Alpha", "diff-insane.osu")
        info = parse_osu_file(p)
        assert info["title"] == "Test Song Alpha", info
        assert info["version"] == "Insane"
        assert info["beatmap_id"] == 111 and info["set_id"] == 1001
        assert info["bpm"] == 120.0, info["bpm"]
        assert info["length_ms"] == 90000
        assert len(info["md5"]) == 32


def test_filters_and_grouping():
    maps = [
        Beatmap(id="a", set_id="1", artist="X", title="Alpha", creator="m",
                diff="Insane", mode_name="osu", stars=5.0, played_local=True, beatmap_id=1),
        Beatmap(id="b", set_id="1", artist="X", title="Alpha", creator="m",
                diff="Normal", mode_name="osu", stars=2.0, played_local=False, beatmap_id=2),
        Beatmap(id="c", set_id="2", artist="Y", title="Beta", creator="n",
                diff="Hard", mode_name="mania", stars=3.0, played_local=False, beatmap_id=3),
    ]
    assert len(apply_filters(maps, Filters(played="played"))) == 1
    assert len(apply_filters(maps, Filters(played="unplayed"))) == 2
    assert len(apply_filters(maps, Filters(mode="mania"))) == 1
    assert len(apply_filters(maps, Filters(star_min=4.0))) == 1
    assert len(apply_filters(maps, Filters(text="beta"))) == 1
    assert len(group_mapsets(maps)) == 2


def test_stable_scan_no_db():
    with tempfile.TemporaryDirectory() as td:
        mock = make_mock_install(td)
        maps = scan_stable(mock["songs"], "", "")
        assert len(maps) == 3, [m.diff for m in maps]
        assert all(not m.played for m in maps)  # no db -> all unplayed
        assert {m.set_id for m in maps} == {"1001", "1002"}


class MvpTests(unittest.TestCase):
    def test_osu_parser(self):
        test_osu_parser()

    def test_filters_and_grouping(self):
        test_filters_and_grouping()

    def test_stable_scan_no_db(self):
        test_stable_scan_no_db()
