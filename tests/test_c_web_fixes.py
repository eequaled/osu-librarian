"""Node-side checks for C-tier web fixes (pure helpers). Skipped when node is absent."""
import json
import os
import shutil
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
Promise.all([import("./web/views.js"), import("./web/bulk.js")]).then(([v, b]) => {
  const out = {
    maxToasts: v.MAX_TOASTS,
    rowId0: v.rowDomId(0),
    rowId7: v.rowDomId(7),
    dis0: b.isExportDisabled(0),
    dis3: b.isExportDisabled(3)
  };
  console.log(JSON.stringify(out));
}).catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class CWebFixesTests(unittest.TestCase):
    def test_pure_helpers(self):
        r = subprocess.run(["node", "-e", SCRIPT], capture_output=True,
                           text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["maxToasts"], 3)
        self.assertEqual(out["rowId0"], "list-row-0")
        self.assertEqual(out["rowId7"], "list-row-7")
        self.assertTrue(out["dis0"])
        self.assertFalse(out["dis3"])

    def test_syntax(self):
        for f in ("web/views.js", "web/bulk.js", "web/main.js"):
            r = subprocess.run(["node", "--check", f], capture_output=True,
                               text=True, cwd=REPO, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
