"""Finding 4: /api/status must avoid Beatmap rebuilds + re-globbing installs."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402
from src.library import from_dict_list, summarize  # noqa: E402

_ROWS = [
    {"id": "a", "set_id": "s1", "played_local": True, "played_online": False},
    {"id": "b", "set_id": "s1", "played_local": False, "played_online": True},
    {"id": "c", "set_id": "s2", "played_local": False, "played_online": False},
]


class StatusCheapTests(unittest.TestCase):
    def test_summarize_rows_matches(self):
        self.assertEqual(server._summarize_rows(_ROWS),
                         summarize(from_dict_list(_ROWS)))

    def test_installs_cached_with_ttl(self):
        server._installs_cache["at"] = 0.0
        server._installs_cache["items"] = []
        with mock.patch("src.detect.find_installs", return_value=[]) as f:
            server._cached_installs()
            server._cached_installs()
            self.assertEqual(f.call_count, 1)
        server._installs_cache["at"] = time.time() - 61.0
        with mock.patch("src.detect.find_installs", return_value=[]) as f:
            server._cached_installs()
            self.assertEqual(f.call_count, 1)

    def test_status_counts_without_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                server._save_scan_atomic("stable", ["a", "b", "c"], _ROWS,
                                         {"files": {}})
                server._installs_cache["at"] = 0.0
                server._installs_cache["items"] = []
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                port = httpd.server_address[1]
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    with mock.patch("src.detect.find_installs",
                                    return_value=[]) as f:
                        for _ in range(2):
                            with urllib.request.urlopen(
                                    f"http://127.0.0.1:{port}/api/status",
                                    timeout=10) as r:
                                status = json.loads(r.read().decode())
                    self.assertEqual(f.call_count, 1)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                self.assertEqual(status["counts"],
                                 {"diffs": 3, "sets": 2, "played": 2,
                                  "unplayed": 1})
            finally:
                os.chdir(cwd)
                server._installs_cache["at"] = 0.0
                server._installs_cache["items"] = []


if __name__ == "__main__":
    unittest.main()
