"""Node-side checks for web/auth.js pure relay helpers. Skipped when node is absent."""
import json
import os
import shutil
import subprocess
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
import("./web/auth.js").then(a => {
  const out = {
    url: a.buildRelayAuthorizeUrl("https://relay.example/", 123, "a".repeat(64), "8787"),
    httpPort: a.relayPort({port: "", protocol: "http:"}),
    httpsPort: a.relayPort({port: "", protocol: "https:"}),
    explicit: a.relayPort({port: "8787", protocol: "http:"}),
    ok: a.validRelayTarget("a".repeat(64), "8787"),
    badTicket: a.validRelayTarget("xyz", "8787"),
    emptyTicket: a.validRelayTarget("", "8787"),
    emptyPort: a.validRelayTarget("a".repeat(64), ""),
    zeroPort: a.validRelayTarget("a".repeat(64), "0"),
    bigPort: a.validRelayTarget("a".repeat(64), "65536")
  };
  console.log(JSON.stringify(out));
}).catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class AuthJsTests(unittest.TestCase):
    def test_relay_port_and_target(self):
        r = subprocess.run(["node", "-e", SCRIPT], capture_output=True,
                           text=True, cwd=REPO, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("state=" + "a" * 64 + ".8787", out["url"])
        self.assertEqual(out["httpPort"], "80")
        self.assertEqual(out["httpsPort"], "443")
        self.assertEqual(out["explicit"], "8787")
        self.assertTrue(out["ok"])
        self.assertFalse(out["badTicket"])
        self.assertFalse(out["emptyTicket"])
        self.assertFalse(out["emptyPort"])
        self.assertFalse(out["zeroPort"])
        self.assertFalse(out["bigPort"])

    def test_syntax(self):
        for f in ("web/auth.js", "web/main.js"):
            r = subprocess.run(["node", "--check", f], capture_output=True,
                               text=True, cwd=REPO, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
