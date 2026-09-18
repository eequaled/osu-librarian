"""Node-side checks for web/store.js pure helpers. Skipped when node is absent."""
import json
import os
import shutil
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
import("./web/store.js").then(s => {
  const out = {
    debounceMs: s.STAR_REBUILD_DEBOUNCE_MS,
    crossedMin: s.resolveStarRange(7, 5, "min"),
    crossedMax: s.resolveStarRange(7, 5, "max"),
    crossedUnk: s.resolveStarRange(7, 5, ""),
    normal: s.resolveStarRange(2, 8, "min"),
    equal: s.resolveStarRange(5, 5, "max")
  };
  console.log(JSON.stringify(out));
}).catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class StoreHelperTests(unittest.TestCase):
    def test_star_range(self):
        r = subprocess.run(["node", "-e", SCRIPT], capture_output=True,
                           text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertGreaterEqual(out["debounceMs"], 150)
        self.assertLessEqual(out["debounceMs"], 200)
        self.assertEqual(out["crossedMin"], [7, 7])
        self.assertEqual(out["crossedMax"], [5, 5])
        self.assertEqual(out["crossedUnk"], [5, 5])
        self.assertEqual(out["normal"], [2, 8])
        self.assertEqual(out["equal"], [5, 5])


if __name__ == "__main__":
    unittest.main()
