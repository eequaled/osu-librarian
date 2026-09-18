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
    def _scan(self, mode, fresh=True):
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
                                       realm_export="", out=out, fresh=fresh)
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

    def test_scan_no_fresh_warns_but_still_guards(self):
        rc, err, out, exists = self._scan("stable", fresh=False)
        self.assertEqual(rc, 2)
        self.assertIn("--no-fresh", err)
        self.assertFalse(exists)

    def test_scan_help_mentions_fresh(self):
        r = subprocess.run([sys.executable, "main.py", "scan", "--help"],
                           capture_output=True, text=True, cwd=REPO,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--fresh", r.stdout)
        self.assertIn("--no-fresh", r.stdout)
        self.assertIn("always", r.stdout.lower())


class CliBehaviorTests(unittest.TestCase):
    def _mainmod(self):
        sys.path.insert(0, REPO)
        import main as mainmod
        return mainmod

    def test_auth_missing_client_id_exit2(self):
        mainmod = self._mainmod()
        from src.config import Settings, ApiSettings
        orig = mainmod.load_settings
        mainmod.load_settings = lambda path="settings.json": Settings(
            api=ApiSettings(client_id=0, client_secret=""))
        try:
            a = argparse.Namespace(client_id=0, client_secret="",
                                   redirect_uri="", code="x", save_token="")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = mainmod.cmd_auth(a)
        finally:
            mainmod.load_settings = orig
        self.assertEqual(rc, 2)

    def test_check_online_missing_token_exit2(self):
        mainmod = self._mainmod()
        from src.config import Settings
        with tempfile.TemporaryDirectory() as td:
            lib = os.path.join(td, "library.json")
            with open(lib, "w", encoding="utf-8") as f:
                f.write("[]")
            orig = mainmod.load_settings
            mainmod.load_settings = lambda path="settings.json": Settings()
            try:
                a = argparse.Namespace(
                    in_path=lib, out=os.path.join(td, "o.json"),
                    client_id=0, client_secret="", user_id=123,
                    token="", code="", refresh="", public_only=False,
                    cache=os.path.join(td, "c.json"))
                err = io.StringIO()
                with contextlib.redirect_stdout(err):
                    rc = mainmod.cmd_check_online(a)
            finally:
                mainmod.load_settings = orig
        self.assertEqual(rc, 2)
        self.assertIn("need --token", err.getvalue())

    def test_check_online_missing_user_id_exit2(self):
        mainmod = self._mainmod()
        from src.config import Settings
        with tempfile.TemporaryDirectory() as td:
            lib = os.path.join(td, "library.json")
            with open(lib, "w", encoding="utf-8") as f:
                f.write("[]")
            orig = mainmod.load_settings
            mainmod.load_settings = lambda path="settings.json": Settings()
            try:
                a = argparse.Namespace(
                    in_path=lib, out=os.path.join(td, "o.json"),
                    client_id=0, client_secret="", user_id=0,
                    token="tok", code="", refresh="", public_only=False,
                    cache=os.path.join(td, "c.json"))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = mainmod.cmd_check_online(a)
            finally:
                mainmod.load_settings = orig
        self.assertEqual(rc, 2)
        self.assertIn("need --user-id", out.getvalue())

    def test_report_corrupt_library_raises(self):
        mainmod = self._mainmod()
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "library.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("{not json")
            a = argparse.Namespace(in_path=bad,
                                   out=os.path.join(td, "r.html"))
            with self.assertRaises(ValueError):
                mainmod.cmd_report(a)

    def test_check_online_corrupt_library_raises(self):
        mainmod = self._mainmod()
        from src.config import Settings
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "library.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("[oops")
            orig = mainmod.load_settings
            mainmod.load_settings = lambda path="settings.json": Settings()
            try:
                a = argparse.Namespace(
                    in_path=bad, out=os.path.join(td, "o.json"),
                    client_id=0, client_secret="", user_id=1,
                    token="tok", code="", refresh="", public_only=False,
                    cache=os.path.join(td, "c.json"))
                with self.assertRaises(ValueError):
                    mainmod.cmd_check_online(a)
            finally:
                mainmod.load_settings = orig

    def test_ui_missing_library_raises(self):
        mainmod = self._mainmod()
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "nope.json")
            a = argparse.Namespace(in_path=missing)
            with self.assertRaises(OSError):
                mainmod.cmd_ui(a)


if __name__ == "__main__":
    unittest.main()
