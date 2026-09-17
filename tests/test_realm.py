"""Consumer tests for realm export wiring. No node, no real client.realm."""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import cache as cachemod  # noqa: E402
from src import server as servermod  # noqa: E402
from src.lazer_scanner import (  # noqa: E402
    _parse_blob,
    load_realm_export,
    parse_lazer_blob,
)
from src.library import Beatmap, from_dict_list  # noqa: E402
from src.osu_parser import parse_osu_file  # noqa: E402
from src.server import Handler  # noqa: E402

MD5_A = "a" * 32
MD5_B = "b" * 32
MD5_C = "c" * 32
HASH_BG = "0123456789abcdef" * 4  # 64 hex
HASH_OTHER = "fedcba9876543210" * 4


def _dump_obj():
    return {
        "beatmaps": [
            {
                "onlineId": 111, "setOnlineId": 1001, "md5": MD5_A,
                "stars": 5.0, "status": 1, "statusName": "ranked",
                "mode": "osu", "difficultyName": "Insane",
                "title": "T", "artist": "A", "creator": "C",
                "bpm": 120.0, "length": 90000,
                "dateAdded": "2025-01-02T03:04:05.000Z",
                "plays": 2, "grade": "A",
            },
            {
                "onlineId": 112, "setOnlineId": 1001, "md5": MD5_B,
                "stars": 2.0, "status": 4, "statusName": "loved",
                "mode": "taiko", "difficultyName": "Normal",
                "title": "T", "artist": "A", "creator": "C",
                "bpm": 100.0, "length": 60000,
                "dateAdded": "2025-01-03T00:00:00.000Z",
                "plays": 0, "grade": "",
            },
            {
                "onlineId": -1, "setOnlineId": -1, "md5": MD5_C,
                "stars": 0.0, "status": 99, "mode": "osu",
                "difficultyName": "Local", "title": "L", "artist": "L",
                "creator": "L", "bpm": 0.0, "length": 0,
                "dateAdded": "", "plays": 0, "grade": "",
            },
        ],
        "scores": [
            {"beatmapOnlineId": 111, "beatmapMd5": MD5_A,
             "ruleset": "osu", "rank": 3},
        ],
        "sets": {
            "1001": [
                {"filename": "bg.jpg", "hash": HASH_BG},
                {"filename": "song.mp3", "hash": "1" * 64},
            ],
            "1002": [
                {"filename": "other.png", "hash": HASH_OTHER},
                {"filename": "track.mp3", "hash": "2" * 64},
            ],
            "1003": [
                {"filename": "track.mp3", "hash": "3" * 64},
                {"filename": "map.osu", "hash": "4" * 64},
            ],
        },
        "meta": {"realmVersion": 51, "exportedAt": "2025-01-01T00:00:00.000Z"},
    }


def _write_dump(path, obj=None):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj if obj is not None else _dump_obj(), f)


OSU_TMPL = """osu file format v14
[General]
Mode: 0
[Metadata]
Title:T
Artist:A
Creator:C
Version:{diff}
BeatmapID:{bid}
BeatmapSetID:{sid}
[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
[TimingPoints]
0,500,4,2,0,80,1,0
[Events]
{events}
[HitObjects]
256,192,1000,1,0,0:0:0:0:
"""


def _write_osu(path, bid=111, sid=1001, events='0,0,"bg.jpg",0,0', diff="Insane"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(OSU_TMPL.format(bid=bid, sid=sid, events=events, diff=diff))


class RealmExportShapeTests(unittest.TestCase):
    def test_backward_compat_keys(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "realm.json")
            _write_dump(p, {"beatmaps": [{"onlineId": 5, "stars": 3.5}],
                            "scores": [{"beatmapOnlineId": 5,
                                        "beatmapMd5": MD5_A}]})
            r = load_realm_export(p)
            self.assertIn(5, r["played_ids"])
            self.assertIn(MD5_A, r["played_md5"])
            self.assertEqual(r["stars"][5], 3.5)
            # new keys exist even for old dumps
            for k in ("grades", "statuses", "dates", "set_files"):
                self.assertIn(k, r)

    def test_grades_status_dates_setfiles(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "realm.json")
            _write_dump(p)
            r = load_realm_export(p)
            self.assertEqual(r["grades"][111], "A")
            self.assertEqual(r["grades"][str(111)], "A")
            self.assertEqual(r["grades"][MD5_A], "A")
            self.assertEqual(r["statuses"][111], "ranked")
            self.assertEqual(r["statuses"][112], "loved")
            self.assertEqual(r["dates"][111], "2025-01-02T03:04:05.000Z")
            # unmapped status int -> unknown
            self.assertEqual(r["statuses"][MD5_C], "unknown")
            # set_files under int + string keys
            self.assertIn(1001, r["set_files"])
            self.assertIn("1001", r["set_files"])
            self.assertEqual(r["set_files"][1001][0],
                             {"filename": "bg.jpg", "hash": HASH_BG})

    def test_scores_rank_fallback_grade(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "realm.json")
            _write_dump(p, {
                "beatmaps": [{"onlineId": 200, "md5": MD5_A, "stars": 1.0,
                              "status": 1}],
                "scores": [{"beatmapOnlineId": 200, "beatmapMd5": MD5_A,
                            "rank": 6}],
                "sets": {},
            })
            r = load_realm_export(p)
            # rank 6 -> X
            self.assertEqual(r["grades"][200], "X")


class ParseBlobTests(unittest.TestCase):
    def _realm(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.json")
            _write_dump(p)
            return load_realm_export(p)

    def test_onlineid_match(self):
        realm = self._realm()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, bid=111, sid=1001, events='0,0,"bg.jpg",0,0')
            b = parse_lazer_blob(p, realm)
            self.assertEqual(b.beatmap_id, 111)
            self.assertEqual(b.ranked, "ranked")
            self.assertEqual(b.grade, "A")
            self.assertEqual(b.date_added, "2025-01-02T03:04:05.000Z")
            self.assertEqual(b.bg, "bg.jpg")
            self.assertEqual(b.bg_hash, HASH_BG)
            self.assertTrue(b.played_local)

    def test_md5_fallback(self):
        realm = self._realm()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            # blob claims local id but content md5 won't match; instead craft
            # realm keyed only by md5: use MD5_C entry (onlineId -1)
            _write_osu(p, bid=-1, sid=-1, events="")
            # compute its md5 and inject a grade under that md5
            import hashlib as _h
            with open(p, "rb") as f:
                md5 = _h.md5(f.read()).hexdigest()
            realm["grades"][md5] = "S"
            realm["statuses"][md5] = "qualified"
            realm["dates"][md5] = "2025-05-05T00:00:00.000Z"
            b = parse_lazer_blob(p, realm)
            self.assertEqual(b.grade, "S")
            self.assertEqual(b.ranked, "qualified")
            self.assertEqual(b.date_added, "2025-05-05T00:00:00.000Z")

    def test_bg_exact_case_insensitive(self):
        realm = self._realm()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, bid=111, sid=1001, events='0,0,"BG.JPG",0,0')
            b = parse_lazer_blob(p, realm)
            self.assertEqual(b.bg, "BG.JPG")
            self.assertEqual(b.bg_hash, HASH_BG)

    def test_bg_fallback_first_image(self):
        realm = self._realm()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            # 112 lives in set 1001 which has bg.jpg; use set 1002 instead
            _write_osu(p, bid=999, sid=1002, events='0,0,"missing.jpg",0,0')
            # inject status so lookup hits set 1002 manifest
            b = parse_lazer_blob(p, realm)
            self.assertEqual(b.bg, "missing.jpg")
            self.assertEqual(b.bg_hash, HASH_OTHER)

    def test_bg_unresolvable_empty(self):
        realm = self._realm()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, bid=998, sid=1003, events='0,0,"missing.jpg",0,0')
            b = parse_lazer_blob(p, realm)
            self.assertEqual(b.bg_hash, "")
            # no bg at all -> empty hash
            p2 = os.path.join(td, "n.osu")
            _write_osu(p2, bid=998, sid=1003, events="// nothing")
            b2 = parse_lazer_blob(p2, realm)
            self.assertEqual(b2.bg, "")
            self.assertEqual(b2.bg_hash, "")

    def test_unknown_status_fallback(self):
        realm = self._realm()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, bid=113, sid=9999, events="")
            b = parse_lazer_blob(p, realm)
            self.assertEqual(b.ranked, "unknown")
            self.assertEqual(b.grade, "")
            self.assertEqual(b.date_added, "")

    def test_none_status_maps_to_unknown_ranked(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.json")
            _write_dump(p, {
                "beatmaps": [{"onlineId": 300, "setOnlineId": 7,
                              "md5": MD5_A, "stars": 0.0, "status": -3,
                              "statusName": "none", "plays": 0, "grade": "",
                              "dateAdded": ""}],
                "scores": [], "sets": {},
            })
            realm = load_realm_export(p)
            self.assertEqual(realm["statuses"][300], "none")
            op = os.path.join(td, "m.osu")
            _write_osu(op, bid=300, sid=7, events="")
            b = parse_lazer_blob(op, realm)
            self.assertEqual(b.ranked, "unknown")

    def test_parse_blob_bg(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, events='0,0,"bg.jpg",0,0')
            self.assertEqual(_parse_blob(p).get("bg"), "bg.jpg")
            p2 = os.path.join(td, "n.osu")
            _write_osu(p2, events="// nothing")
            self.assertEqual(_parse_blob(p2).get("bg", ""), "")


class OsuParserBgTests(unittest.TestCase):
    def test_events_bg(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, events='0,0,"bg.jpg",0,0')
            self.assertEqual(parse_osu_file(p)["bg"], "bg.jpg")

    def test_events_bg_missing(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, events="// nothing")
            self.assertEqual(parse_osu_file(p)["bg"], "")

    def test_video_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.osu")
            _write_osu(p, events='1,0,"video.mp4",0,0')
            self.assertEqual(parse_osu_file(p)["bg"], "")


class LibraryFieldTests(unittest.TestCase):
    def test_new_fields_defaults(self):
        b = Beatmap(id="x", set_id="y")
        self.assertEqual(b.bg, "")
        self.assertEqual(b.bg_hash, "")
        self.assertEqual(b.date_added, "")

    def test_from_dict_list_picks_up(self):
        rows = [{"id": "x", "set_id": "y", "bg": "a.jpg",
                 "bg_hash": HASH_BG, "date_added": "2025-01-01T00:00:00.000Z"}]
        maps = from_dict_list(rows)
        self.assertEqual(maps[0].bg, "a.jpg")
        self.assertEqual(maps[0].bg_hash, HASH_BG)
        self.assertEqual(maps[0].date_added, "2025-01-01T00:00:00.000Z")


def _req(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class ArtEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        servermod.set_mode("stable")

    def tearDown(self):
        try:
            servermod.set_mode("stable")
        except Exception:
            pass
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def _serve(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        return httpd, port

    def test_stable_art_ok_and_404s(self):
        songs = os.path.join(self.tmp.name, "Songs")
        folder = os.path.join(songs, "1001 Artist - Title")
        os.makedirs(folder)
        bg_path = os.path.join(folder, "bg.jpg")
        with open(bg_path, "wb") as f:
            f.write(b"\xff\xd8\xff fake jpeg")
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": songs}, f)
        servermod.set_mode("stable")
        rows = [{"id": "row1", "set_id": "1001",
                 "folder": "1001 Artist - Title", "bg": "bg.jpg",
                 "bg_hash": "", "origin": "stable"}]
        cachemod.save_scan("stable", ["k"], rows, {"files": {"k": [1, 2]}})
        httpd, port = self._serve()
        try:
            base = f"http://127.0.0.1:{port}"
            code, headers, body = _req("GET", base + "/api/art?id=row1")
            self.assertEqual(code, 200, body[:200])
            self.assertIn("image/jpeg", headers.get("Content-Type", ""))
            self.assertTrue(headers.get("ETag"))
            self.assertEqual(headers.get("Cache-Control"),
                             "public,max-age=86400")
            self.assertEqual(body, b"\xff\xd8\xff fake jpeg")
            code, _, _ = _req("GET", base + "/api/art?id=nope")
            self.assertEqual(code, 404)
            code, _, body = _req("GET", base + "/api/art")
            self.assertEqual(code, 404)
            self.assertIn(b"error", body)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_stable_art_traversal_blocked(self):
        songs = os.path.join(self.tmp.name, "Songs")
        os.makedirs(os.path.join(songs, "F"))
        with open("settings.json", "w") as f:
            json.dump({"mode": "stable", "songs_dir": songs}, f)
        servermod.set_mode("stable")
        rows = [{"id": "evil", "set_id": "1", "folder": "F",
                 "bg": "../evil.jpg", "origin": "stable"}]
        cachemod.save_scan("stable", ["k"], rows, {"files": {"k": [1, 1]}})
        httpd, port = self._serve()
        try:
            code, _, body = _req("GET",
                                 f"http://127.0.0.1:{port}/api/art?id=evil")
            # basename() strips the traversal -> F/evil.jpg missing -> 404
            self.assertEqual(code, 404)
            self.assertIn(b"error", body)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_lazer_art_hash_layout(self):
        lazer = os.path.join(self.tmp.name, "lazer")
        h = HASH_BG.lower()
        blob_dir = os.path.join(lazer, "files", h[0], h[:2])
        os.makedirs(blob_dir)
        with open(os.path.join(blob_dir, h), "wb") as f:
            f.write(b"\x89PNG fake")
        with open("settings.json", "w") as f:
            json.dump({"mode": "lazer", "lazer_dir": lazer}, f)
        servermod.set_mode("lazer")
        rows = [{"id": "lz1", "set_id": "5", "bg": "bg.png",
                 "bg_hash": h, "origin": "lazer"}]
        cachemod.save_scan("lazer", ["k"], rows, {"files": {"k": [1, 1]}})
        httpd, port = self._serve()
        try:
            base = f"http://127.0.0.1:{port}"
            code, headers, body = _req("GET", base + "/api/art?id=lz1")
            self.assertEqual(code, 200, body[:200])
            # Content-Type comes from the bg filename extension
            self.assertIn("image/png", headers.get("Content-Type", ""))
            self.assertTrue(headers.get("ETag"))
            self.assertEqual(body, b"\x89PNG fake")
            code, _, body = _req("GET", base + "/api/art?id=missing")
            self.assertEqual(code, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_lazer_art_404_no_hash(self):
        lazer = os.path.join(self.tmp.name, "lazer")
        os.makedirs(os.path.join(lazer, "files"))
        with open("settings.json", "w") as f:
            json.dump({"mode": "lazer", "lazer_dir": lazer}, f)
        servermod.set_mode("lazer")
        rows = [{"id": "lz2", "set_id": "5", "bg": "bg.jpg",
                 "bg_hash": "", "origin": "lazer"}]
        cachemod.save_scan("lazer", ["k"], rows, {"files": {"k": [1, 1]}})
        httpd, port = self._serve()
        try:
            code, _, body = _req("GET",
                                 f"http://127.0.0.1:{port}/api/art?id=lz2")
            self.assertEqual(code, 404)
            self.assertIn(b"error", body)
        finally:
            httpd.shutdown()
            httpd.server_close()


class RunnerTests(unittest.TestCase):
    def test_returns_none_without_node(self):
        from src import realm_export as rexp
        with tempfile.TemporaryDirectory() as td:
            lazer = os.path.join(td, "lazer")
            os.makedirs(lazer)
            with open(os.path.join(lazer, "client.realm"), "wb") as f:
                f.write(b"fake")
            with mock.patch.object(rexp.shutil, "which", return_value=None):
                self.assertIsNone(rexp.export_realm(lazer, os.path.join(td, "c")))

    def test_cache_hit_no_node_needed(self):
        from src import realm_export as rexp
        with tempfile.TemporaryDirectory() as td:
            lazer = os.path.join(td, "lazer")
            os.makedirs(lazer)
            rp = os.path.join(lazer, "client.realm")
            with open(rp, "wb") as f:
                f.write(b"fake-realm")
            st = os.stat(rp)
            cdir = os.path.join(td, "cache")
            os.makedirs(cdir)
            cached = {"beatmaps": [], "scores": [],
                      "sets": {}, "meta": {}}
            with open(os.path.join(cdir, f"realm-{st.st_mtime_ns}.json"),
                      "w") as f:
                json.dump(cached, f)
            with mock.patch.object(rexp.shutil, "which",
                                   side_effect=AssertionError("no node")):
                with mock.patch.object(rexp.subprocess, "run",
                                       side_effect=AssertionError("no run")):
                    out = rexp.export_realm(lazer, cdir)
            self.assertEqual(out, cached)

    def test_missing_realm_returns_none(self):
        from src import realm_export as rexp
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(rexp.export_realm(os.path.join(td, "nope"),
                                                os.path.join(td, "c")))

    def test_node_failure_returns_none(self):
        from src import realm_export as rexp
        with tempfile.TemporaryDirectory() as td:
            lazer = os.path.join(td, "lazer")
            os.makedirs(lazer)
            with open(os.path.join(lazer, "client.realm"), "wb") as f:
                f.write(b"fake")
            helper = rexp._helper_dir()
            os.makedirs(helper, exist_ok=True)
            # minimal export.mjs presence is provided by repo; force node found
            # but the run itself fails.
            with mock.patch.object(rexp.shutil, "which",
                                   return_value="/usr/bin/node"):
                with mock.patch.object(rexp, "_ensure_node_modules",
                                       return_value=True):
                    proc = mock.Mock()
                    proc.returncode = 1
                    proc.stdout = b""
                    proc.stderr = b"boom"
                    with mock.patch.object(rexp.subprocess, "run",
                                           return_value=proc):
                        self.assertIsNone(
                            rexp.export_realm(lazer, os.path.join(td, "c")))


if __name__ == "__main__":
    unittest.main()
