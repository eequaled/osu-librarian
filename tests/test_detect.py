import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import detect  # noqa: E402


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("HOME")
        self.old_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["HOME"] = self.tmp.name
        os.environ.pop("XDG_DATA_HOME", None)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        if self.old_xdg is not None:
            os.environ["XDG_DATA_HOME"] = self.old_xdg
        self.tmp.cleanup()

    def _mk(self, *parts, is_file=False):
        p = os.path.join(self.tmp.name, *parts)
        if is_file:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("x")
        else:
            os.makedirs(p, exist_ok=True)
        return p

    def test_finds_wine_stable(self):
        songs = self._mk(".wine", "drive_c", "users", "me", "AppData", "Local",
                         "osu!", "Songs", "123 Artist - Title")
        self._mk(".wine", "drive_c", "users", "me", "AppData", "Local",
                 "osu!", "osu!.db", is_file=True)
        with open(os.path.join(songs, "a.osu"), "w") as f:
            f.write("osu file format v14\n")
        installs = detect.find_installs()
        stables = [i for i in installs if i.kind == "stable"]
        self.assertTrue(stables, installs)
        self.assertTrue(stables[0].songs and stables[0].osu_db)

    def test_finds_lazer(self):
        self._mk(".local", "share", "osu", "files", "ab")
        self._mk(".local", "share", "osu", "client.realm", is_file=True)
        installs = detect.find_installs()
        lazers = [i for i in installs if i.kind == "lazer"]
        self.assertTrue(lazers, installs)
        self.assertTrue(lazers[0].files and lazers[0].realm)

    def test_ignores_empty_dirs(self):
        self._mk(".local", "share", "osu")  # no markers
        installs = detect.find_installs()
        self.assertEqual([i for i in installs if i.kind == "lazer"], [])


if __name__ == "__main__":
    unittest.main()
