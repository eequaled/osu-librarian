"""Finding 6: /api/art must index rows and stream file bytes in chunks."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class ArtTests(unittest.TestCase):
    def test_indexed_lookup_and_streamed_body(self):
        import urllib.error  # noqa: F401 (used in _get)
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                songs = os.path.join(tmp, "Songs")
                folder = os.path.join(songs, "1 Artist - Title")
                os.makedirs(folder)
                big = os.urandom(200 * 1024)  # bigger than one 64k chunk
                with open(os.path.join(folder, "bg.jpg"), "wb") as f:
                    f.write(big)
                rows = [{"id": "row-1", "set_id": "s1", "folder": "1 Artist - Title",
                         "bg": "bg.jpg"},
                        {"id": "row-2", "set_id": "s1"}]
                with open("settings.json", "w") as f:
                    json.dump({"mode": "stable", "songs_dir": songs,
                               "osu_db": "", "scores_db": ""}, f)
                server._save_scan_atomic("stable", ["a.osu", None], rows,
                                         {"files": {}})
                server._art_index["key"] = None
                server._art_index["by_id"] = {}
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                port = httpd.server_address[1]
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    base = f"http://127.0.0.1:{port}/api/art?id=row-1"
                    code, headers, body = _get(base)
                    self.assertEqual(code, 200)
                    self.assertEqual(body, big)
                    self.assertEqual(int(headers.get("Content-Length")),
                                     len(big))
                    first_key = server._art_index["key"]
                    self.assertIsNotNone(first_key)
                    # second hit reuses the index (no rebuild, same object)
                    before = server._art_index["by_id"]
                    code, _, body = _get(base)
                    self.assertEqual(code, 200)
                    self.assertEqual(body, big)
                    self.assertIs(server._art_index["by_id"], before)
                    self.assertEqual(server._art_index["key"], first_key)
                    # conditional request still works
                    code, _, _ = _get(base, {"If-None-Match":
                                             headers.get("ETag")})
                    self.assertEqual(code, 304)
                    # unknown id + missing background still 404
                    code, _, _ = _get(f"http://127.0.0.1:{port}/api/art?id=nope")
                    self.assertEqual(code, 404)
                    code, _, _ = _get(f"http://127.0.0.1:{port}/api/art?id=row-2")
                    self.assertEqual(code, 404)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
            finally:
                os.chdir(cwd)
                server._art_index["key"] = None
                server._art_index["by_id"] = {}


if __name__ == "__main__":
    unittest.main()
