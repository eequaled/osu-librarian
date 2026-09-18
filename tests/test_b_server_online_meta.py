"""Finding 2: online-check worker stats must surface on the job object + API."""
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

_STATS = {"checked": 1, "from_cache": 0, "played": 0, "errors": 1,
          "skipped_no_id": 0}


def _seed_library():
    rows = [{"id": "abc", "set_id": "s1", "beatmap_id": 111,
             "played_local": False, "played_online": False,
             "artist": "A", "title": "T"}]
    server._save_scan_atomic("stable", ["k.osu"], rows, {"files": {}})


class OnlineMetaTests(unittest.TestCase):
    def test_stats_on_job_and_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                _seed_library()
                with mock.patch.object(server, "valid_token", return_value="tok"), \
                     mock.patch.object(server, "_load_token",
                                        return_value={"user_id": 7}), \
                     mock.patch("src.osu_api.mark_online_played",
                                return_value=dict(_STATS)) as m:
                    jid, err = server.run_online_check()
                    self.assertEqual(err, "")
                    self.assertTrue(jid)
                    end = time.time() + 10
                    while time.time() < end:
                        job = server.registry.get(jid)
                        if job is not None and job.state in ("done", "error"):
                            break
                        time.sleep(0.05)
                    job = server.registry.get(jid)
                    self.assertEqual(job.state, "done", job.to_dict())
                    self.assertEqual(getattr(job, "meta", None), _STATS)
                    self.assertEqual(job.to_dict().get("meta"), _STATS)
                    self.assertTrue(m.called)
                # job-status API response includes the stashed stats
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                port = httpd.server_address[1]
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/jobs/{jid}",
                            timeout=10) as r:
                        payload = json.loads(r.read().decode())
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                self.assertEqual(payload.get("meta"), _STATS)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
