import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as mainmod


class CliAuthTests(unittest.TestCase):
    def test_auth_output_has_no_token(self):
        fake_tok = {"access_token": "SECRET_ACCESS_ABC",
                    "refresh_token": "SECRET_REFRESH_XYZ",
                    "expires_in": 3600}
        with tempfile.TemporaryDirectory() as td:
            save = os.path.join(td, "tok.json")
            a = argparse.Namespace(client_id=123, client_secret="s",
                                   redirect_uri="http://localhost/x",
                                   code="code123", save_token=save)
            with mock.patch("main.exchange_code", return_value=dict(fake_tok)):
                with mock.patch("main.get_me", return_value={"id": 42}):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        rc = mainmod.cmd_auth(a)
            text = out.getvalue()
            self.assertEqual(rc, 0)
            self.assertNotIn("SECRET_ACCESS_ABC", text)
            self.assertNotIn("SECRET_REFRESH_XYZ", text)
            self.assertNotIn("access_token", text)
            self.assertNotIn("refresh_token", text)
            self.assertIn("user_id", text)
            self.assertIn("42", text)
            self.assertIn("obtained_at", text)
            self.assertTrue(os.path.exists(save))

    def test_auth_output_no_save_still_clean(self):
        fake_tok = {"access_token": "TOKEN_KEEP_SECRET",
                    "refresh_token": "REF_KEEP_SECRET",
                    "expires_in": 3600}
        a = argparse.Namespace(client_id=123, client_secret="s",
                               redirect_uri="http://localhost/x",
                               code="code123", save_token="")
        with mock.patch("main.exchange_code", return_value=dict(fake_tok)):
            with mock.patch("main.get_me", return_value={"id": 7}):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = mainmod.cmd_auth(a)
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertNotIn("TOKEN_KEEP_SECRET", text)
        self.assertNotIn("REF_KEEP_SECRET", text)


if __name__ == "__main__":
    unittest.main()
