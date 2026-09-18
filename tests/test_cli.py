import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")


class CliTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "main.py", *args],
                              capture_output=True, text=True, cwd=REPO, timeout=60)

    def test_top_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in ("scan", "report", "ui", "web", "auth", "check-online"):
            self.assertIn(cmd, r.stdout)

    def test_each_subcommand_help(self):
        for cmd in ("scan", "report", "ui", "web", "auth", "check-online"):
            r = self._run(cmd, "--help")
            self.assertEqual(r.returncode, 0, f"{cmd}: {r.stderr}")


class ScanGuardTests(unittest.TestCase):
    def _scan(self, mode):
        sys.path.insert(0, REPO)
        import main as mainmod
        from src.config import Settings
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "library.json")
            orig = mainmod.load_settings
            mainmod.load_settings = lambda path="settings.json": Settings(
                mode=mode, stable_dir="", songs_dir="", osu_db="",
                scores_db="", lazer_dir="", realm_export="")
            try:
                a = argparse.Namespace(mode=mode, songs="", db=None,
                                       scores=None, lazer_dir="",
                                       realm_export="", out=out)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = mainmod.cmd_scan(a)
            finally:
                mainmod.load_settings = orig
            return rc, err.getvalue(), out, os.path.exists(out)

    def test_scan_stable_empty_songs_no_write(self):
        rc, err, out, exists = self._scan("stable")
        self.assertEqual(rc, 2)
        self.assertTrue(err.strip(), "expected a stderr message")
        self.assertFalse(exists, f"{out} must not be written on empty input")

    def test_scan_lazer_empty_dir_no_write(self):
        rc, err, out, exists = self._scan("lazer")
        self.assertEqual(rc, 2)
        self.assertTrue(err.strip(), "expected a stderr message")
        self.assertFalse(exists, f"{out} must not be written on empty input")


if __name__ == "__main__":
    unittest.main()
