import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.osu_api as api


class FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, retry_after=None):
    fp = io.BytesIO(b"{}")
    hdrs = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    # HTTPMessage-like dict works with .get
    return urllib.error.HTTPError("http://x/", code, "err", hdrs, fp)


class BackoffTests(unittest.TestCase):
    def test_retry_after_honored(self):
        err = _http_error(429, retry_after="2")
        ok = FakeResp(200, {"scores": []})
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]) as m:
            with mock.patch("src.osu_api.time.sleep") as slp:
                status, body = api._get("http://x/", "tok")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"scores": []})
        slp.assert_called_once()
        self.assertAlmostEqual(slp.call_args[0][0], 2.0)

    def test_retry_after_capped(self):
        err = _http_error(429, retry_after="9999")
        ok = FakeResp(200, {"scores": []})
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]):
            with mock.patch("src.osu_api.time.sleep") as slp:
                api._get("http://x/", "tok")
        self.assertLessEqual(slp.call_args[0][0], api._RETRY_AFTER_CAP_S)

    def test_exponential_backoff_on_500(self):
        err = _http_error(500)
        ok = FakeResp(200, {"scores": []})
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]):
            with mock.patch("src.osu_api.time.sleep") as slp:
                status, _ = api._get("http://x/", "tok")
        self.assertEqual(status, 200)
        slp.assert_called_once()
        self.assertAlmostEqual(slp.call_args[0][0], 0.4)

    def test_return_shape_kept(self):
        maps = []
        import types
        b = types.SimpleNamespace(beatmap_id=1, played_online=False)
        maps.append(b)
        with mock.patch("src.osu_api.user_has_scores", return_value=False):
            with mock.patch("src.osu_api.time.sleep"):
                with mock.patch("src.osu_api.save_cache"):
                    stats = api.mark_online_played(maps, 7, "tok",
                                                   cache_path="/tmp/nope.json")
        self.assertEqual(set(stats), {"checked", "from_cache", "played",
                                      "errors", "skipped_no_id"})


if __name__ == "__main__":
    unittest.main()
