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
    equal: s.resolveStarRange(5, 5, "max"),
    countMixed: s.countVisibleSelection([
      { type: "map", map: { id: 1 } },
      { type: "set", set: { maps: [{ id: 2 }, { id: 3 }] } },
      { type: "map", map: { id: 4 } }
    ], new Set([1, 3])),
    countEmpty: s.countVisibleSelection([], new Set([1])),
    countAll: s.countVisibleSelection([
      { type: "map", map: { id: 1 } },
      { type: "set", set: { maps: [{ id: 2 }] } }
    ], new Set([1, 2])),
    scopeButton: s.listShortcutAllowed("BUTTON", false, false),
    scopeDialogInput: s.listShortcutAllowed("INPUT", false, false),
    scopeSelect: s.listShortcutAllowed("SELECT", false, false),
    scopeList: s.listShortcutAllowed("DIV", true, false),
    scopeBody: s.listShortcutAllowed("BODY", false, true),
    scopeNothing: s.listShortcutAllowed("", false, false)
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
        self.assertEqual(out["countMixed"], {"total": 4, "sel": 2})
        self.assertEqual(out["countEmpty"], {"total": 0, "sel": 0})
        self.assertEqual(out["countAll"], {"total": 2, "sel": 2})
        self.assertFalse(out["scopeButton"])
        self.assertFalse(out["scopeDialogInput"])
        self.assertFalse(out["scopeSelect"])
        self.assertTrue(out["scopeList"])
        self.assertTrue(out["scopeBody"])
        self.assertTrue(out["scopeNothing"])


if __name__ == "__main__":
    unittest.main()
