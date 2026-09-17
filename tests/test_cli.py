import os
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
