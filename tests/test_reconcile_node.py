"""Node-side checks for web/reconcile.js pure logic (skipped without node).

Covers the lazer-count verdict: null until a number is entered, exact-match
wording, and mismatch wording that accounts for every map in the gap
(spec: unplayed-count-reconciliation R2.2).
"""
import json
import os
import shutil
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
import("./web/reconcile.js").then(m => {
  const j = {available: true, total: 10, local_played: 3, online_only: 4,
             unplayed: 3, lazer_style_unplayed: 7};
  const out = {
    empty: m.lazerVerdictHtml(j, NaN),
    match: m.lazerVerdictHtml(j, 7),
    mismatchMore: m.lazerVerdictHtml(j, 5),
    mismatchFewer: m.lazerVerdictHtml(j, 9),
  };
  console.log(JSON.stringify(out));
});
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ReconcileVerdictTests(unittest.TestCase):
    def test_verdict_wording(self):
        r = subprocess.run(["node", "-e", SCRIPT], capture_output=True,
                           text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIsNone(out["empty"])
        self.assertIn("fully explained by 4 online-only", out["match"])
        self.assertIn("does not match", out["mismatchMore"])
        self.assertIn("derives 7 — 2 more", out["mismatchMore"])
        self.assertIn("2 fewer", out["mismatchFewer"])


if __name__ == "__main__":
    unittest.main()
