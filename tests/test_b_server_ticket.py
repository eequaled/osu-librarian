"""Finding 9: local ticket validation must require exactly 64 hex chars."""
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402


class TicketTests(unittest.TestCase):
    def test_regex_matches_relay_contract(self):
        ok = "ab12" * 16
        self.assertTrue(server._TICKET_RE.match(ok))
        self.assertTrue(server._TICKET_RE.match("AB12" * 16))
        for bad in ("", "abc", "a" * 63, "a" * 65, "a" * 128,
                    "g" * 64, "a" * 63 + "!", " a" * 32):
            self.assertIsNone(server._TICKET_RE.match(bad), bad)

    def test_relay_return_rejects_short_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                port = httpd.server_address[1]
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    for ticket in ("abc", "a" * 128):
                        url = (f"http://127.0.0.1:{port}/api/auth/relay-return"
                               f"?ticket={ticket}")
                        try:
                            with urllib.request.urlopen(url, timeout=10):
                                self.fail(f"accepted {ticket!r}")
                        except urllib.error.HTTPError as e:
                            self.assertEqual(e.code, 400)
                            self.assertIn("invalid ticket",
                                          e.read().decode())
                finally:
                    httpd.shutdown()
                    httpd.server_close()
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
