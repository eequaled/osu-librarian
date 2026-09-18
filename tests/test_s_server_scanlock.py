import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.server as servermod


class ScanLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        with open("settings.json", "w", encoding="utf-8") as f:
            json.dump({"mode": "stable", "songs_dir": "", "osu_db": "",
                       "scores_db": ""}, f)
        # release lock if a previous test leaked it
        try:
            servermod._scan_lock.release()
        except RuntimeError:
            pass
        while not servermod._scan_lock.acquire(blocking=False):
            time.sleep(0.01)
        servermod._scan_lock.release()

    def tearDown(self):
        try:
            servermod._scan_lock.release()
        except RuntimeError:
            pass
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_second_scan_while_locked_returns_empty(self):
        self.assertTrue(servermod._scan_lock.acquire(blocking=False))
        try:
            self.assertEqual(servermod.run_scan("stable", False), "")
        finally:
            servermod._scan_lock.release()

    def test_online_check_busy_returns_409_message(self):
        with open(".token.json", "w", encoding="utf-8") as f:
            json.dump({"access_token": "t", "expires_in": 3600,
                       "obtained_at": time.time(), "user_id": 1}, f)
        servermod._save_scan_atomic("stable", ["k"], [{"id": "x"}], {"files": {}})
        servermod._scan_lock.acquire(blocking=False)
        try:
            jid, err = servermod.run_online_check()
            self.assertEqual(jid, "")
            self.assertEqual(err, "scan already in progress")
        finally:
            servermod._scan_lock.release()

    def test_save_is_atomic_no_partial_file(self):
        servermod._save_scan_atomic("stable", ["k"], [{"id": "x"}], {"files": {"k": [1]}})
        self.assertTrue(os.path.exists(".cache/scan-stable.json"))
        with open(".cache/scan-stable.json", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["keys"], ["k"])
        # os.replace used for atomic writes
        import inspect
        src = inspect.getsource(servermod._save_scan_atomic) + inspect.getsource(servermod._atomic_write_json)
        self.assertIn("os.replace", src)
        self.assertIn("mkstemp", src)

    def test_lock_released_after_scan(self):
        jid = servermod.run_scan("stable", True)
        self.assertTrue(jid)
        end = time.time() + 10
        while time.time() < end:
            job = servermod.registry.get(jid)
            if job is not None and job.state in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertTrue(servermod._scan_lock.acquire(blocking=False))
        servermod._scan_lock.release()

    def test_http_scan_returns_409_when_busy(self):
        import urllib.request
        from http.server import ThreadingHTTPServer
        from src.server import Handler
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            servermod._scan_lock.acquire(blocking=False)
            try:
                data = json.dumps({"mode": "stable"}).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/scan", data=data,
                    headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        self.fail(f"expected 409 got {r.status}")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 409)
                    body = json.loads(e.read().decode())
                    try:
                        e.close()
                    except Exception:
                        pass
                    self.assertIn("progress", body.get("error", "").lower())
            finally:
                servermod._scan_lock.release()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
