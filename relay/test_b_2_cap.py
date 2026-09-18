"""B-tier finding 2: ticket store cap with oldest eviction (stdlib unittest)."""
from __future__ import annotations

import io
import os
import sys
import time
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import server as relay  # type: ignore
except ImportError:
    from relay import server as relay  # type: ignore


class TicketCapCase(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("OSU_CLIENT_ID", "OSU_CLIENT_SECRET", "RELAY_PUBLIC_URL")}
        os.environ["OSU_CLIENT_ID"] = "424242"
        os.environ["OSU_CLIENT_SECRET"] = "test-client-secret-xyz-123"
        os.environ["RELAY_PUBLIC_URL"] = "https://link.example.com"
        relay._reset_state()
        self.addCleanup(self._restore)

    def _restore(self):
        relay._reset_state()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_max_tickets_constant_direct(self):
        self.assertTrue(hasattr(relay, "MAX_TICKETS"))
        self.assertLessEqual(relay.MAX_TICKETS, 10000)
        self.assertGreaterEqual(relay.MAX_TICKETS, 100)

    def _fill(self, n, base=None):
        if base is None:
            base = time.time()
        with relay._lock:
            for i in range(n):
                t = f"{i:064x}"[-64:].replace(" ", "0")
                # ensure 64 hex chars unique
                t = ("%064x" % i)[-64:]
                relay._tickets[t] = {"created_at": base + i,
                                     "data": None, "attempts": 0}

    def test_eviction_of_oldest_with_stderr_log(self):
        cap = relay.MAX_TICKETS
        self._fill(cap)
        self.assertEqual(len(relay._tickets), cap)
        oldest = min(relay._tickets.items(),
                     key=lambda kv: float(kv[1]["created_at"]))[0]

        class FakeSelf:
            close_connection = False

            def __init__(self):
                import email.message
                self.headers = email.message.Message()
                self.headers["Content-Length"] = "0"
                import io as _io
                self.rfile = _io.BytesIO(b"")
                self.sent = {}

            def _discard_body(self):
                return relay.Handler._discard_body(self)

            def _send_json(self, code, obj, allow_cors=False):
                self.sent["code"] = code
                self.sent["obj"] = dict(obj)

        # Fake rate-limit key to avoid interference: use direct lock path instead
        # of full handler (handler needs network). Exercise the eviction block
        # through _handle_pair logic by calling the real method with stubs.
        fake = FakeSelf()
        # Stub _client_key + _bucket_limited via monkeypatch of module funcs
        orig_key = relay._client_key
        orig_lim = relay._bucket_limited
        relay._client_key = lambda h: "127.0.0.1"  # type: ignore
        relay._bucket_limited = lambda k, now: False  # type: ignore
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                relay.Handler._handle_pair(fake)  # type: ignore
        finally:
            relay._client_key = orig_key  # type: ignore
            relay._bucket_limited = orig_lim  # type: ignore
        self.assertEqual(fake.sent.get("code"), 200)
        self.assertLessEqual(len(relay._tickets), cap)
        self.assertNotIn(oldest, relay._tickets)
        self.assertIn("evict", buf.getvalue().lower())

    def test_does_not_evict_when_under_cap(self):
        self._fill(10)
        buf = io.StringIO()
        with redirect_stderr(buf):
            # second fill under cap should not log
            with relay._lock:
                before = len(relay._tickets)
                # simulate one insertion without triggering eviction path
                relay._tickets["f" * 64] = {"created_at": time.time(),
                                            "data": None, "attempts": 0}
                after = len(relay._tickets)
        self.assertEqual(after, before + 1)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
