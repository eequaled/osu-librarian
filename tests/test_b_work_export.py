"""B-tier regression: collection export skips short ids with a count."""
import io
import os
import struct
import sys
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import export as exp


def _count_in_payload(payload: bytes) -> int:
    # version(4) + ncoll(4) + enc_string(name) + count(4) + entries
    off = 8
    assert payload[off] == 0x0B, "expected collection.db string marker"
    off += 1
    n = 0
    shift = 0
    while True:
        chunk = payload[off]
        off += 1
        n |= (chunk & 0x7F) << shift
        shift += 7
        if not (chunk & 0x80):
            break
    off += n
    (count,) = struct.unpack_from("<I", payload, off)
    return count


class ExportSkipTests(unittest.TestCase):
    def test_validates_ids(self):
        self.assertTrue(exp.is_collection_id("a" * 32))
        self.assertTrue(exp.is_collection_id("A" * 32))  # hex, any case
        self.assertFalse(exp.is_collection_id("short"))
        self.assertFalse(exp.is_collection_id("z" * 32))  # non-hex
        self.assertFalse(exp.is_collection_id(None))
        self.assertFalse(exp.is_collection_id(123))
        self.assertFalse(exp.is_collection_id(""))

    def test_skip_count_and_stderr(self):
        rows = [
            {"id": "a" * 32},
            {"id": "short"},                       # path/short fallback
            {"id": "/songs/folder/diff.osu"},      # path fallback
            {"id": "b" * 32},
            {"no": "id"},                           # missing id
            {"id": "z" * 32},                       # non-hex 32 chars
        ]
        err = io.StringIO()
        with redirect_stderr(err):
            payload, stats = exp.to_collection_db_with_counts(rows)
        self.assertEqual(stats, {"total": 6, "exported": 2, "skipped": 4})
        self.assertIn("skipped 4/6", err.getvalue())
        self.assertEqual(_count_in_payload(payload), 2)

    def test_no_stderr_when_clean(self):
        err = io.StringIO()
        with redirect_stderr(err):
            payload, stats = exp.to_collection_db_with_counts([{"id": "c" * 32}])
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(_count_in_payload(payload), 1)

    def test_legacy_wrapper_still_bytes(self):
        err = io.StringIO()
        with redirect_stderr(err):
            payload = exp.to_collection_db([{"id": "d" * 32}, {"id": "x"}])
        self.assertIsInstance(payload, bytes)
        self.assertEqual(_count_in_payload(payload), 1)
        self.assertIn("skipped 1/2", err.getvalue())

    def test_collection_stats_helper(self):
        rows = [{"id": "e" * 32}, {"id": "nope"}]
        self.assertEqual(exp.collection_stats(rows),
                         {"total": 2, "exported": 1, "skipped": 1})

    def test_id_contract_documented(self):
        with open(os.path.join(os.path.dirname(__file__), "..",
                               "src", "library.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ID CONTRACT", src)
        self.assertIn("beatmap_id", src)


if __name__ == "__main__":
    unittest.main()
