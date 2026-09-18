"""Unit tests for relay _client_key TRUST_PROXY handling (stdlib unittest)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # `discover -s relay` imports as top-level modules
    import server as relay  # type: ignore
except ImportError:
    from relay import server as relay  # type: ignore


class FakeHandler:
    def __init__(self, peer="10.0.0.99", headers=None):
        self.client_address = (peer, 12345)
        # Plain dict on purpose: exercises case-insensitive lookup.
        self.headers = dict(headers) if headers is not None else {}


class ClientKeyCase(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TRUST_PROXY")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TRUST_PROXY", None)
        else:
            os.environ["TRUST_PROXY"] = self._old

    def test_no_header_falls_back_to_peer(self):
        os.environ["TRUST_PROXY"] = "1"
        h = FakeHandler(peer="10.0.0.99", headers={})
        self.assertEqual(relay._client_key(h), "10.0.0.99")

    def test_garbage_header_falls_back_to_peer(self):
        os.environ["TRUST_PROXY"] = "1"
        for bad in ("not-an-ip", "unknown", "", "   ", "1.2.3.4.5.6!!!",
                    "<script>", "garbage, 1.2.3.4"):
            with self.subTest(header=bad):
                h = FakeHandler(peer="10.0.0.99",
                                headers={"X-Forwarded-For": bad})
                self.assertEqual(relay._client_key(h), "10.0.0.99")

    def test_single_ip_when_trusted(self):
        os.environ["TRUST_PROXY"] = "1"
        h = FakeHandler(peer="10.0.0.99",
                        headers={"X-Forwarded-For": "1.2.3.4"})
        self.assertEqual(relay._client_key(h), "1.2.3.4")

    def test_single_ip_whitespace_stripped(self):
        os.environ["TRUST_PROXY"] = "1"
        h = FakeHandler(peer="10.0.0.99",
                        headers={"X-Forwarded-For": "  5.6.7.8  "})
        self.assertEqual(relay._client_key(h), "5.6.7.8")

    def test_chained_xff_uses_leftmost_when_trusted(self):
        os.environ["TRUST_PROXY"] = "1"
        h = FakeHandler(peer="10.0.0.99",
                        headers={"X-Forwarded-For":
                                 "9.9.9.9, 10.0.0.1, 127.0.0.1"})
        self.assertEqual(relay._client_key(h), "9.9.9.9")

    def test_chained_xff_ignored_when_untrusted(self):
        os.environ["TRUST_PROXY"] = "0"
        h = FakeHandler(peer="10.0.0.99",
                        headers={"X-Forwarded-For":
                                 "9.9.9.9, 10.0.0.1, 127.0.0.1"})
        self.assertEqual(relay._client_key(h), "10.0.0.99")

    def test_default_unset_ignores_xff(self):
        os.environ.pop("TRUST_PROXY", None)
        h = FakeHandler(peer="10.0.0.99",
                        headers={"X-Forwarded-For": "1.2.3.4"})
        self.assertEqual(relay._client_key(h), "10.0.0.99")

    def test_lowercase_header_name(self):
        os.environ["TRUST_PROXY"] = "1"
        h = FakeHandler(peer="10.0.0.99",
                        headers={"x-forwarded-for": "2.3.4.5"})
        self.assertEqual(relay._client_key(h), "2.3.4.5")


if __name__ == "__main__":
    unittest.main()
