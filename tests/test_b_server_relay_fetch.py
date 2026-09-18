"""Finding 10: POST /token first with JSON ticket, GET fallback for old relays."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import server  # noqa: E402

_TICKET = "ab12" * 16
_TOKEN = {"access_token": "tok", "token_type": "Bearer"}


class _Resp:
    def __init__(self, obj):
        self._raw = json.dumps(obj).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError("http://relay/token", code, "err", {}, io.BytesIO(b"{}"))


class RelayFetchTests(unittest.TestCase):
    def test_post_first_with_json_ticket(self):
        seen = []

        def _fake(req, timeout=None):
            seen.append(req)
            self.assertEqual(req.get_method(), "POST")
            self.assertTrue(req.full_url.endswith("/token"))
            self.assertEqual(json.loads(req.data.decode()), {"ticket": _TICKET})
            return _Resp(_TOKEN)

        with mock.patch("urllib.request.urlopen", side_effect=_fake):
            ok, body = server._fetch_relay_token("http://relay/", _TICKET)
        self.assertTrue(ok)
        self.assertEqual(body, _TOKEN)
        self.assertEqual(len(seen), 1)

    def test_fallback_to_get_on_404_and_405(self):
        for code in (404, 405):
            calls = []

            def _fake(req, timeout=None, _code=code):
                calls.append(req)
                if req.get_method() == "POST":
                    raise _http_error(_code)
                self.assertEqual(req.get_method(), "GET")
                self.assertIn("ticket=", req.full_url)
                return _Resp(_TOKEN)

            with mock.patch("urllib.request.urlopen", side_effect=_fake):
                ok, body = server._fetch_relay_token("http://relay", _TICKET)
            self.assertTrue(ok, code)
            self.assertEqual(body, _TOKEN, code)
            self.assertEqual(len(calls), 2, code)

    def test_fallback_to_get_on_connection_error(self):
        calls = []

        def _fake(req, timeout=None):
            calls.append(req)
            if req.get_method() == "POST":
                raise urllib.error.URLError("conn refused")
            return _Resp(_TOKEN)

        with mock.patch("urllib.request.urlopen", side_effect=_fake):
            ok, body = server._fetch_relay_token("http://relay", _TICKET)
        self.assertTrue(ok)
        self.assertEqual(body, _TOKEN)
        self.assertEqual(len(calls), 2)

    def test_no_fallback_on_ticket_errors(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(410)) as u:
            ok, err = server._fetch_relay_token("http://relay", _TICKET)
        self.assertFalse(ok)
        self.assertIn("expired or already used", err)
        self.assertEqual(u.call_count, 1)

    def test_post_without_token_is_expired(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_Resp({"error": "gone"})) as u:
            ok, err = server._fetch_relay_token("http://relay", _TICKET)
        self.assertFalse(ok)
        self.assertIn("expired or already used", err)
        self.assertEqual(u.call_count, 1)


if __name__ == "__main__":
    unittest.main()
