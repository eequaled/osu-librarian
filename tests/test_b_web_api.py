"""Node-side checks for web/api.js export error handling. Skipped when node is absent."""
import json
import os
import shutil
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
import("./web/api.js").then(a => {
  const out = {
    serverDetail: a.exportFailureMessage(400, { error: "too many ids" }),
    emptyBody: a.exportFailureMessage(500, {}),
    nullBody: a.exportFailureMessage(503, null),
    htmlBody: a.exportFailureMessage(502, null)
  };
  console.log(JSON.stringify(out));
}).catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ExportErrorTests(unittest.TestCase):
    def test_failure_message(self):
        r = subprocess.run(["node", "-e", SCRIPT], capture_output=True,
                           text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["serverDetail"], "too many ids")
        self.assertEqual(out["emptyBody"], "export 500")
        self.assertEqual(out["nullBody"], "export 503")
        self.assertEqual(out["htmlBody"], "export 502")


if __name__ == "__main__":
    unittest.main()
