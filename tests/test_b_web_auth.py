"""Node-side checks for web/auth.js pair-ticket TTL helpers. Skipped when node is absent."""
import json
import os
import shutil
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
import("./web/auth.js").then(a => {
  const out = {
    ttl600: a.pairTtlMs(600),
    ttlStr: a.pairTtlMs("300"),
    ttlMissing: a.pairTtlMs(undefined),
    ttlNull: a.pairTtlMs(null),
    ttlZero: a.pairTtlMs(0),
    ttlNeg: a.pairTtlMs(-5),
    ttlJunk: a.pairTtlMs("junk"),
    countdown90: a.formatPairCountdown(90000),
    countdownRoundUp: a.formatPairCountdown(89500),
    countdownZero: a.formatPairCountdown(0),
    countdownPast: a.formatPairCountdown(-1000)
  };
  console.log(JSON.stringify(out));
}).catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PairTtlTests(unittest.TestCase):
    def test_ttl_and_countdown(self):
        r = subprocess.run(["node", "-e", SCRIPT], capture_output=True,
                           text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["ttl600"], 600000)
        self.assertEqual(out["ttlStr"], 300000)
        self.assertEqual(out["ttlMissing"], 600000)
        self.assertEqual(out["ttlNull"], 600000)
        self.assertEqual(out["ttlZero"], 600000)
        self.assertEqual(out["ttlNeg"], 600000)
        self.assertEqual(out["ttlJunk"], 600000)
        self.assertIn("90s", out["countdown90"])
        self.assertIn("90s", out["countdownRoundUp"])
        self.assertIn("0s", out["countdownZero"])
        self.assertIn("0s", out["countdownPast"])


if __name__ == "__main__":
    unittest.main()
