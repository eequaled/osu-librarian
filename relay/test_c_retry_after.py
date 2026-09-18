"""C-tier regression tests: 429 responses carry Retry-After + reset info.

Stdlib unittest only, no external network. Run:

    python3 -m unittest discover -s relay
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # `discover -s relay` imports this file as top-level `test_c_retry_after`
    import server as relay  # type: ignore
except ImportError:  # `discover` from repo root / direct `relay.test_c_retry_after`
    from relay import server as relay  # type: ignore


class RetryAfterCase(unittest.TestCase):
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

    def _base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _post_pair_full(self):
        req = urllib.request.Request(self._base() + "/pair", data=b"", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, dict(e.headers), e.read()
            finally:
                try:
                    e.close()
                except Exception:
                    pass

    def _get_full(self, path: str):
        req = urllib.request.Request(self._base() + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, dict(e.headers), e.read()
            finally:
                try:
                    e.close()
                except Exception:
                    pass

    def _assert_rate_limited(self, code, headers, body):
        self.assertEqual(code, 429)
        lower = {str(k).lower(): v for k, v in headers.items()}
        retry_after = lower.get("retry-after")
        self.assertIsNotNone(retry_after, "429 must carry a Retry-After header")
        try:
            secs = int(str(retry_after).strip())
        except (TypeError, ValueError):
            self.fail(f"Retry-After header is not an int: {retry_after!r}")
        self.assertGreaterEqual(secs, 1)
        self.assertLessEqual(secs, relay.RATE_LIMIT_WINDOW)
        obj = json.loads(body.decode() or "{}")
        self.assertEqual(obj.get("error"), "rate_limited")
        self.assertIn("retry_after", obj)
        self.assertEqual(int(obj["retry_after"]), secs)

    def test_pair_429_has_retry_after(self):
        for _ in range(relay.RATE_LIMIT_MAX):
            code, _h, _b = self._post_pair_full()
            self.assertEqual(code, 200)
        code, headers, body = self._post_pair_full()
        self._assert_rate_limited(code, headers, body)

    def test_token_429_has_retry_after(self):
        for _ in range(relay.RATE_LIMIT_MAX):
            code, _h, _b = self._get_full("/token?ticket=" + "a" * 64)
            self.assertEqual(code, 404)
        code, headers, body = self._get_full("/token?ticket=" + "a" * 64)
        self._assert_rate_limited(code, headers, body)


if __name__ == "__main__":
    unittest.main()
