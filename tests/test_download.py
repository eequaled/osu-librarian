"""Bulk download tests (spec §8). No network: all mirror I/O mocked."""
import json
import os
import struct
import sys
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, REPO)

from src.download import (dedupe, download_set, lookup_md5, parse_collection_db,
                          parse_id_list, resolve_md5s, run_download)


def _enc_string(s: str) -> bytes:
    b = s.encode("utf-8")
    out = bytearray([0x0B])
    n = len(b)
    while True:
        chunk = n & 0x7F
        n >>= 7
        out.append(chunk | (0x80 if n else 0))
        if not n:
            break
    return bytes(out) + b


def _make_collection_db(path: str, collections, version: int = 20160212) -> None:
    out = bytearray()
    out += struct.pack("<I", version)
    out += struct.pack("<I", len(collections))
    for name, md5s in collections:
        out += _enc_string(name)
        out += struct.pack("<I", len(md5s))
        for m in md5s:
            out += _enc_string(m)
    with open(path, "wb") as f:
        f.write(bytes(out))


MD5_A = "a" * 32
MD5_B = "b" * 32
MD5_C = "c" * 32


class CollectionParseTests(unittest.TestCase):
    def test_parse_single_collection(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "c.db")
            _make_collection_db(p, [("sami dz - !eequaled", [MD5_A, MD5_B, MD5_C])])
            cols = parse_collection_db(p)
            self.assertEqual(len(cols), 1)
            self.assertEqual(cols[0]["name"], "sami dz - !eequaled")
            self.assertEqual(cols[0]["md5s"], [MD5_A, MD5_B, MD5_C])

    def test_parse_multi_collection(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "c.db")
            _make_collection_db(p, [("one", [MD5_A]), ("two", [MD5_B, MD5_C])])
            cols = parse_collection_db(p)
            self.assertEqual([c["name"] for c in cols], ["one", "two"])
            self.assertEqual(cols[1]["md5s"], [MD5_B, MD5_C])

    def test_parse_truncated_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "c.db")
            with open(p, "wb") as f:
                f.write(struct.pack("<I", 20160212) + struct.pack("<I", 1) + b"\x0b")
            with self.assertRaises(ValueError):
                parse_collection_db(p)


class IdListTests(unittest.TestCase):
    def test_urls_and_md5s(self):
        text = ("https://osu.ppy.sh/beatmapsets/137338\n"
                "https://osu.ppy.sh/beatmaps/343302\n"
                + MD5_A + "\n# comment\n")
        parsed = parse_id_list(text)
        self.assertIn(137338, parsed["sets"])
        self.assertIn(343302, parsed["maps"])
        self.assertIn(MD5_A, parsed["md5s"])

    def test_dedupes(self):
        parsed = parse_id_list(f"{MD5_A}\n{MD5_A}\n137338\n137338\n")
        self.assertEqual(parsed["md5s"], [MD5_A])
        self.assertEqual(parsed["sets"], [137338])


class MirrorTests(unittest.TestCase):
    def _patch_fetch(self, responses):
        import src.download as dl
        orig = dl._fetch_json
        calls = []

        def fake(url, timeout=30.0):
            calls.append(url)
            for prefix, resp in responses:
                if url.startswith(prefix):
                    if isinstance(resp, Exception):
                        raise resp
                    return resp
            return None

        dl._fetch_json = fake
        return orig, calls

    def test_primary_ok(self):
        import src.download as dl
        orig, calls = self._patch_fetch([
            ("https://osu.direct/api/md5/", {"ParentSetID": 137338, "BeatmapID": 343302, "DiffName": "Insane"}),
        ])
        try:
            sid, bid, diff = lookup_md5(MD5_A)
        finally:
            dl._fetch_json = orig
        self.assertEqual(sid, 137338)
        self.assertEqual(bid, 343302)
        self.assertEqual(diff, "Insane")
        self.assertEqual(len(calls), 1)

    def test_fallback_on_primary_fail(self):
        import src.download as dl
        orig, calls = self._patch_fetch([
            ("https://osu.direct/api/md5/", None),
            ("https://catboy.best/api/md5/", {"ParentSetID": 200176, "BeatmapID": 1}),
        ])
        try:
            sid, bid, _ = lookup_md5(MD5_B)
        finally:
            dl._fetch_json = orig
        self.assertEqual(sid, 200176)
        self.assertEqual(len(calls), 2)

    def test_none_after_both_fail(self):
        import src.download as dl
        orig, calls = self._patch_fetch([
            ("https://osu.direct/api/md5/", None),
            ("https://catboy.best/api/md5/", None),
        ])
        try:
            sid, bid, diff = lookup_md5(MD5_C)
        finally:
            dl._fetch_json = orig
        self.assertIsNone(sid)
        self.assertIsNone(bid)
        self.assertIsNone(diff)

    def test_bad_md5_no_network(self):
        import src.download as dl
        orig, calls = self._patch_fetch([("", {"ParentSetID": 1})])
        try:
            self.assertEqual(lookup_md5("zzz"), (None, None, None))
        finally:
            dl._fetch_json = orig
        self.assertEqual(calls, [])


class DedupThrottleTests(unittest.TestCase):
    def test_same_set_dedupes_to_one_download(self):
        md5s = [f"{i:032x}" for i in range(10)]  # 10 unique md5s
        lookups = []

        def fake_lookup(md5):
            lookups.append(md5)
            return 137338, 1000, "X"  # all -> same set

        downloads = []

        def fake_download(sid, d):
            downloads.append(sid)
            return "ok"

        with tempfile.TemporaryDirectory() as td:
            manifest = run_download(
                md5s=md5s, outdir=os.path.join(td, "run"), name="t",
                lookup_fn=fake_lookup, download_fn=fake_download,
                lookup_delay_s=0, download_delay_s=0)
        self.assertEqual(len(lookups), 10)  # one lookup per unique md5
        self.assertEqual(downloads, [137338])  # one download per unique set
        self.assertEqual(manifest["set_ids"], [137338])

    def test_lookup_pacing(self):
        import src.download as dl
        orig_sleep = dl.time.sleep
        slept = []
        dl.time.sleep = lambda s: slept.append(s)
        try:
            resolve_md5s([MD5_A, MD5_B, MD5_C],
                         lookup_fn=lambda m: (1, 2, None), delay_s=0.5)
        finally:
            dl.time.sleep = orig_sleep
        # 3 lookups -> 2 inter-lookup sleeps of >=0.5
        self.assertEqual(len(slept), 2)
        self.assertTrue(all(s >= 0.5 for s in slept))

    def test_dedupe_helper(self):
        self.assertEqual(dedupe([1, 1, 2, 1, 3]), [1, 2, 3])


class DownloadSetTests(unittest.TestCase):
    def test_skip_exists(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "137338.osz")
            with open(p, "wb") as f:
                f.write(b"x" * (101 * 1024))
            self.assertEqual(download_set(137338, td), "exists")

    def test_reject_small_body(self):
        import src.download as dl
        import urllib.request as req

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"tiny"

        orig = req.urlopen
        req.urlopen = lambda *a, **k: FakeResp()
        try:
            with tempfile.TemporaryDirectory() as td:
                st = download_set(137338, td)
        finally:
            req.urlopen = orig
        self.assertTrue(st.startswith("FAIL"))

    def test_ok_writes_zip(self):
        import urllib.request as req

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"PK\x03\x04" + b"z" * (60 * 1024)

        orig = req.urlopen
        req.urlopen = lambda *a, **k: FakeResp()
        try:
            with tempfile.TemporaryDirectory() as td:
                st = download_set(137338, td)
                self.assertEqual(st, "ok")
                self.assertTrue(os.path.isfile(os.path.join(td, "137338.osz")))
                self.assertFalse(os.path.exists(os.path.join(td, "137338.osz.part")))
        finally:
            req.urlopen = orig


class CollectionInvariantTests(unittest.TestCase):
    def _run(self, td, md5s, **kw):
        def fake_lookup(md5):
            if md5 == MD5_C:
                return None, None, None  # missing
            return 137338, 1, "X"
        kw.setdefault("lookup_fn", fake_lookup)
        kw.setdefault("download_fn", lambda sid, d: "ok")
        kw.setdefault("lookup_delay_s", 0)
        kw.setdefault("download_delay_s", 0)
        return run_download(md5s=md5s, outdir=os.path.join(td, "run"),
                            name="run1", **kw)

    def test_always_emits_collection_with_full_list(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._run(td, [MD5_A, MD5_B, MD5_C])
            run = os.path.join(td, "run")
            self.assertTrue(os.path.isfile(os.path.join(run, "collection.db")))
            self.assertTrue(os.path.isfile(os.path.join(run, "mapping.json")))
            self.assertTrue(os.path.isfile(os.path.join(run, "missing.txt")))
            # collection.db re-parses to ALL requested md5s (incl. missing)
            cols = parse_collection_db(os.path.join(run, "collection.db"))
            self.assertEqual(len(cols), 1)
            self.assertEqual(sorted(cols[0]["md5s"]),
                             sorted([MD5_A, MD5_B, MD5_C]))
            # missing subset reported, never silent
            self.assertEqual(manifest["missing"], [MD5_C])
            with open(os.path.join(run, "missing.txt")) as f:
                self.assertIn(MD5_C, f.read())

    def test_only_list_still_emits_collection(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = self._run(td, [MD5_A, MD5_B], only_list=True)
            run = os.path.join(td, "run")
            self.assertTrue(os.path.isfile(os.path.join(run, "collection.db")))
            self.assertEqual(manifest["download"], {})
            cols = parse_collection_db(os.path.join(run, "collection.db"))
            self.assertEqual(sorted(cols[0]["md5s"]), sorted([MD5_A, MD5_B]))

    def test_default_name_and_mapping_shape(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = run_download(
                md5s=[MD5_A], outdir=os.path.join(td, "r"),
                source_name="sami dz - !eequaled",
                lookup_fn=lambda m: (137338, 1, None),
                download_fn=lambda s, d: "ok",
                lookup_delay_s=0, download_delay_s=0)
            self.assertEqual(manifest["name"], "sami dz - !eequaled")
            for k in ("set_ids", "md5_info", "missing", "collections",
                      "diff_count", "export_stats"):
                self.assertIn(k, manifest)
            with open(os.path.join(td, "r", "mapping.json")) as f:
                self.assertEqual(json.load(f)["name"], "sami dz - !eequaled")


if __name__ == "__main__":
    unittest.main()
