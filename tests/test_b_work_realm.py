"""B-tier regression: realm dumps pruned, progress reported, npm locked."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import realm_export as rexp


def _touch(path, mtime_ns):
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    os.utime(path, ns=(mtime_ns, mtime_ns))


class PruneTests(unittest.TestCase):
    def test_prune_keeps_last_n(self):
        with tempfile.TemporaryDirectory() as td:
            base = 1_700_000_000_000_000_000
            for i in range(5):
                _touch(os.path.join(td, f"realm-{base + i}.json"), base + i)
            pruned = rexp._prune_old_dumps(td, keep=3)
            self.assertEqual(pruned, 2)
            left = sorted(n for n in os.listdir(td) if n.startswith("realm-"))
            self.assertEqual(len(left), 3)
            self.assertIn(f"realm-{base + 4}.json", left)
            self.assertIn(f"realm-{base + 3}.json", left)
            self.assertNotIn(f"realm-{base}.json", left)

    def test_prune_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = 1_700_000_000_000_000_000
            for i in range(4):
                _touch(os.path.join(td, f"realm-{base + i}.json"), base + i)
            keep = os.path.join(td, "library.json")
            with open(keep, "w") as f:
                f.write("x")
            rexp._prune_old_dumps(td, keep=3)
            self.assertTrue(os.path.isfile(keep))
            self.assertEqual(len([n for n in os.listdir(td)
                                  if n.startswith("realm-")]), 3)

    def test_export_prunes_after_success(self):
        with tempfile.TemporaryDirectory() as td:
            lazer = os.path.join(td, "lazer")
            os.makedirs(lazer)
            rp = os.path.join(lazer, "client.realm")
            with open(rp, "wb") as f:
                f.write(b"fake")
            st = os.stat(rp)
            cdir = os.path.join(td, "c")
            os.makedirs(cdir)
            base = 1_600_000_000_000_000_000
            for i in range(4):
                _touch(os.path.join(cdir, f"realm-{base + i}.json"), base + i)
            proc = mock.Mock()
            proc.returncode = 0
            proc.stdout = json.dumps({"beatmaps": []}).encode()
            proc.stderr = b""
            with mock.patch.object(rexp.shutil, "which", return_value="/usr/bin/node"):
                with mock.patch.object(rexp, "_ensure_node_modules", return_value=True):
                    with mock.patch.object(rexp, "_helper_dir", return_value=td):
                        # provide export.mjs presence
                        with open(os.path.join(td, "export.mjs"), "w") as f:
                            f.write("// fake")
                        with mock.patch.object(rexp.subprocess, "run", return_value=proc):
                            err = io.StringIO()
                            with redirect_stderr(err):
                                out = rexp.export_realm(lazer, cdir, keep_last=3)
            self.assertIsInstance(out, dict)
            dumps = [n for n in os.listdir(cdir) if n.startswith("realm-")]
            self.assertLessEqual(len(dumps), 3)
            self.assertIn(f"realm-{st.st_mtime_ns}.json", dumps)


class ProgressTests(unittest.TestCase):
    def _good_run(self, lazer, cdir, **kw):
        proc = mock.Mock()
        proc.returncode = 0
        proc.stdout = json.dumps({"beatmaps": []}).encode()
        proc.stderr = b""
        with mock.patch.object(rexp.shutil, "which", return_value="/usr/bin/node"):
            with mock.patch.object(rexp, "_ensure_node_modules", return_value=True):
                with mock.patch.object(rexp, "_helper_dir", return_value=cdir):
                    with open(os.path.join(cdir, "export.mjs"), "w") as f:
                        f.write("// fake")
                    with mock.patch.object(rexp.subprocess, "run", return_value=proc):
                        return rexp.export_realm(lazer, cdir, **kw)

    def test_stderr_progress_line(self):
        with tempfile.TemporaryDirectory() as td:
            lazer = os.path.join(td, "lazer")
            os.makedirs(lazer)
            with open(os.path.join(lazer, "client.realm"), "wb") as f:
                f.write(b"x")
            cdir = os.path.join(td, "c")
            os.makedirs(cdir)
            err = io.StringIO()
            with redirect_stderr(err):
                self._good_run(lazer, cdir)
            self.assertIn("[realm]", err.getvalue())

    def test_callable_progress_gets_stages(self):
        with tempfile.TemporaryDirectory() as td:
            lazer = os.path.join(td, "lazer")
            os.makedirs(lazer)
            with open(os.path.join(lazer, "client.realm"), "wb") as f:
                f.write(b"x")
            cdir = os.path.join(td, "c")
            os.makedirs(cdir)
            seen = []
            err = io.StringIO()
            with redirect_stderr(err):
                self._good_run(lazer, cdir, progress=lambda d, t: seen.append((d, t)))
            self.assertTrue(seen, "progress callback never called")

    def test_job_like_progress_updated(self):
        with tempfile.TemporaryDirectory() as td:
            lazer = os.path.join(td, "lazer")
            os.makedirs(lazer)
            with open(os.path.join(lazer, "client.realm"), "wb") as f:
                f.write(b"x")
            cdir = os.path.join(td, "c")
            os.makedirs(cdir)

            class FakeJob:
                done = 0
                total = 0

            job = FakeJob()
            err = io.StringIO()
            with redirect_stderr(err):
                self._good_run(lazer, cdir, progress=job)
            self.assertEqual((job.done, job.total), (3, 3))


class NpmLockTests(unittest.TestCase):
    def test_lock_object_exists(self):
        self.assertTrue(hasattr(rexp, "_NPM_LOCK"))
        # must act as a lock (acquire/release without deadlock)
        self.assertTrue(rexp._NPM_LOCK.acquire(blocking=False))
        try:
            pass
        finally:
            rexp._NPM_LOCK.release()

    def test_concurrent_install_runs_once(self):
        with tempfile.TemporaryDirectory() as td:
            helper = os.path.join(td, "helper")
            os.makedirs(helper)
            with open(os.path.join(helper, "package.json"), "w") as f:
                f.write("{}")
            nm = os.path.join(helper, "node_modules")
            calls = []

            def fake_run(*a, **k):
                calls.append(1)
                time.sleep(0.2)
                os.makedirs(nm, exist_ok=True)
                m = mock.Mock()
                m.returncode = 0
                return m

            with mock.patch.object(rexp.shutil, "which", return_value="/usr/bin/x"):
                with mock.patch.object(rexp.subprocess, "run", side_effect=fake_run):
                    results = []

                    def target():
                        results.append(rexp._ensure_node_modules(helper))

                    ts = [threading.Thread(target=target) for _ in range(2)]
                    for t in ts:
                        t.start()
                    for t in ts:
                        t.join(timeout=10)
            self.assertEqual(results, [True, True])
            self.assertEqual(len(calls), 1, "npm install must be serialised by the lock")


if __name__ == "__main__":
    unittest.main()
