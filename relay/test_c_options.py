"""C-tier regression tests: OPTIONS preflight is scoped to /pair only.

Only POST /pair is a cross-origin browser endpoint, so only /pair answers
preflight with ACAO + Allow-Methods; every other path must 404 without any
CORS headers. Stdlib unittest only, no external network. Run:

    python3 -m unittest discover -s relay
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # `discover -s relay` imports this file as top-level `test_c_options`
    import server as relay  # type: ignore
except ImportError:  # `discover` from repo root / direct `relay.test_c_options`
    from relay import server as relay  # type: ignore


class OptionsScopeCase(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in
                         ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL", "PORT")}
        os.environ["OSU_CLIENT_ID"] = "424242"
        os.environ["OSU_CLIENT_SECRET"] = "test-client-secret-xyz-123"
        os.environ["RELAY_PUBLIC_URL"] = "https://link.example.com"
        relay._reset_state()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
        relay._reset_state()
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _options(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("OPTIONS", path,
                         headers={"Origin": "http://localhost:8787",
                                  "Access-Control-Request-Method": "POST"})
            resp = conn.getresponse()
            return (resp.status,
                    {str(k).lower(): v for k, v in resp.getheaders()},
                    resp.read())
        finally:
            conn.close()

    def test_options_pair_keeps_preflight(self):
        status, headers, _body = self._options("/pair")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        self.assertIn("access-control-allow-methods", headers)

    def test_options_elsewhere_404_without_cors(self):
        for path in ("/token", "/health", "/auth/callback", "/"):
            with self.subTest(path=path):
                status, headers, body = self._options(path)
                self.assertEqual(status, 404)
                self.assertNotIn("access-control-allow-origin", headers)
                self.assertNotIn("access-control-allow-methods", headers)
                self.assertIn("error", json.loads(body.decode() or "{}"))


if __name__ == "__main__":
    unittest.main()
