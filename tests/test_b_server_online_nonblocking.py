"""Finding 3: POST /api/online-check must return at once; refresh runs in worker."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402


def _seed_library():
    rows = [{"id": "abc", "set_id": "s1", "beatmap_id": 111,
             "played_local": False, "played_online": False}]
    server._save_scan_atomic("stable", ["k.osu"], rows, {"files": {}})


def _wait(jid, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        job = server.registry.get(jid)
        if job is not None and job.state in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job timed out")


class OnlineNonblockingTests(unittest.TestCase):
    def test_returns_before_slow_token_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                _seed_library()

                def _slow_refresh(_settings):
                    time.sleep(1.5)  # stands in for the ~30s network refresh
                    return "tok"

                with mock.patch.object(server, "valid_token",
                                        side_effect=_slow_refresh), \
                     mock.patch.object(server, "_load_token",
                                        return_value={"user_id": 7}), \
                     mock.patch("src.osu_api.mark_online_played",
                                return_value={"checked": 1, "errors": 0,
                                              "from_cache": 0}):
                    start = time.monotonic()
                    jid, err = server.run_online_check()
                    elapsed = time.monotonic() - start
                    self.assertEqual(err, "")
                    self.assertTrue(jid)
                    self.assertLess(elapsed, 1.0,
                                    f"request thread blocked {elapsed:.2f}s on refresh")
                    # mocks stay up until the worker is done
                    self.assertEqual(_wait(jid).state, "done")
            finally:
                os.chdir(cwd)

    def test_dead_token_fails_job_not_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                _seed_library()
                with mock.patch.object(server, "valid_token", return_value=""):
                    jid, err = server.run_online_check()
                self.assertEqual(err, "")
                self.assertTrue(jid)
                job = _wait(jid)
                self.assertEqual(job.state, "error", job.to_dict())
                self.assertIn("link the account again", job.error)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
