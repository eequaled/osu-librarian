"""B-tier finding 1: fail fast when misconfigured (stdlib unittest)."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import server as relay  # type: ignore
except ImportError:
    from relay import server as relay  # type: ignore

FAKE_ID = "424242"
FAKE_SECRET = "test-client-secret-xyz-123"
FAKE_PUBLIC = "https://link.example.com"


class PairConfigCase(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL", "PORT")}
        os.environ["OSU_CLIENT_ID"] = FAKE_ID
        os.environ["OSU_CLIENT_SECRET"] = FAKE_SECRET
        os.environ["RELAY_PUBLIC_URL"] = FAKE_PUBLIC
        relay._reset_state()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        self.port = self.httpd.server_address[1]
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()
        self.addCleanup(self._stop)

    def _stop(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
        relay._reset_state()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _post_pair(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/pair", data=b"", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            finally:
                try:
                    e.close()
                except Exception:
                    pass

    def test_config_error_helper_direct(self):
        self.assertIsNone(relay._config_error())
        os.environ["OSU_CLIENT_ID"] = ""
        self.assertIsNotNone(relay._config_error())
        os.environ["OSU_CLIENT_ID"] = FAKE_ID
        os.environ["OSU_CLIENT_SECRET"] = ""
        self.assertIsNotNone(relay._config_error())
        os.environ["OSU_CLIENT_SECRET"] = FAKE_SECRET
        os.environ["RELAY_PUBLIC_URL"] = ""
        self.assertIsNotNone(relay._config_error())

    def test_pair_503_when_missing_each_var(self):
        for key in ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL"):
            with self.subTest(missing=key):
                saved = os.environ.get(key)
                os.environ[key] = ""
                relay._reset_state()
                code, body = self._post_pair()
                self.assertEqual(code, 503)
                self.assertIn("error", body)
                err = str(body.get("error", ""))
                self.assertIn("relay", err.lower())
                self.assertIn("config", err.lower())
                # restore for next subtest
                if saved is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = saved
                # need good config for next iteration's server (same server reads env live)
                os.environ["OSU_CLIENT_ID"] = FAKE_ID
                os.environ["OSU_CLIENT_SECRET"] = FAKE_SECRET
                os.environ["RELAY_PUBLIC_URL"] = FAKE_PUBLIC

    def test_pair_200_when_configured(self):
        code, body = self._post_pair()
        self.assertEqual(code, 200)
        self.assertIn("ticket", body)

    def test_main_exits_nonzero_and_logs_when_missing(self):
        os.environ["OSU_CLIENT_ID"] = ""
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = relay.main()
        self.assertNotEqual(rc, 0)
        self.assertTrue(buf.getvalue().strip())

    def test_serve_exits_nonzero_when_missing(self):
        os.environ["RELAY_PUBLIC_URL"] = ""
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                relay.serve(port=0)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertTrue(buf.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
