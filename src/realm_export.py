"""Run the node realm_export helper and cache its JSON dump.

export_realm(lazer_dir, cache_dir=".cache") -> dict | None:
  - Copies <lazer_dir>/client.realm to a temp file (never touches the live
    file) and runs `node tools/realm_export/export.mjs <copy>`.
  - Finds node via shutil.which; installs the helper's node_modules once with
    `npm install` (timeout 120s) when missing.
  - Caches the dump at <cache_dir>/realm-<mtime_ns>.json and reuses it while
    client.realm mtime is unchanged.
  - Returns the parsed dict, or None on ANY failure (missing node/npm,
    offline install failure, export error, bad JSON). Callers must fall back
    to blob-sniff behaviour.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading


#: How many realm-*.json dumps to keep in cache_dir (pruned after each export).
REALM_CACHE_KEEP = 3

#: Serialises concurrent `npm install` runs (concurrent scans race otherwise).
_NPM_LOCK = threading.Lock()


def _helper_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "tools", "realm_export")


def _emit_progress(progress, done: int, total: int, stage: str) -> None:
    """Report one realm stage: stderr line + optional job/callback."""
    try:
        print(f"[realm] {stage} ({done}/{total})", file=sys.stderr)
    except Exception:
        pass
    if progress is None:
        return
    try:
        if callable(progress):
            try:
                progress(done, total)
            except TypeError:
                progress(stage)
        else:
            # Job-like object from src/jobs.py (has .done/.total).
            try:
                progress.done = done
                progress.total = total
            except Exception:
                pass
    except Exception:
        pass


def _prune_old_dumps(cache_dir: str, keep: int = REALM_CACHE_KEEP) -> int:
    """Delete older realm-*.json dumps, keeping the newest `keep`."""
    try:
        names = [n for n in os.listdir(cache_dir)
                 if n.startswith("realm-") and n.endswith(".json")]
    except OSError:
        return 0
    if keep < 0:
        keep = 0
    if len(names) <= keep:
        return 0

    def _key(name: str):
        try:
            return (os.stat(os.path.join(cache_dir, name)).st_mtime_ns, name)
        except OSError:
            return (0, name)

    names.sort(key=_key)
    pruned = 0
    for name in names[:-keep] if keep else names:
        try:
            os.unlink(os.path.join(cache_dir, name))
            pruned += 1
        except OSError:
            pass
    return pruned


def _ensure_node_modules(helper: str) -> bool:
    nm = os.path.join(helper, "node_modules")
    if os.path.isdir(nm):
        return True
    with _NPM_LOCK:
        if os.path.isdir(nm):
            return True
        npm = shutil.which("npm")
        node = shutil.which("node")
        if not npm or not node:
            return False
        if not os.path.isfile(os.path.join(helper, "package.json")):
            return False
        try:
            subprocess.run(
                [npm, "install", "--no-audit", "--no-fund"],
                cwd=helper,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=True,
            )
            return os.path.isdir(nm)
        except Exception:
            return False


def export_realm(lazer_dir: str, cache_dir: str = ".cache",
                 progress=None, keep_last: int = REALM_CACHE_KEEP) -> dict | None:
    """See module docstring. `progress` is an optional job/callback fed
    (done, total) per realm stage; every stage also prints a stderr line so
    synchronous node+npm runs never look frozen. `keep_last` bounds how many
    realm-*.json dumps survive in cache_dir (pruned after a good export)."""
    try:
        if not lazer_dir:
            return None
        realm_path = os.path.join(lazer_dir, "client.realm")
        try:
            st = os.stat(realm_path)
        except OSError:
            return None
        mtime_ns = st.st_mtime_ns
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except OSError:
            return None
        cache_path = os.path.join(cache_dir, f"realm-{mtime_ns}.json")
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else None
            except (OSError, ValueError):
                pass  # fall through to re-export
        _emit_progress(progress, 0, 3, "realm export starting")
        node = shutil.which("node")
        if not node:
            return None
        helper = _helper_dir()
        export_js = os.path.join(helper, "export.mjs")
        if not os.path.isfile(export_js):
            return None
        _emit_progress(progress, 1, 3, "ensuring realm_export node_modules")
        if not _ensure_node_modules(helper):
            return None
        # Copy realm to temp; open the copy read-only in node.
        tmp_path = ""
        try:
            _emit_progress(progress, 2, 3, "copying client.realm for node export")
            fd, tmp_path = tempfile.mkstemp(prefix="client-realm-", suffix=".realm")
            os.close(fd)
            shutil.copyfile(realm_path, tmp_path)
        except OSError:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            return None
        try:
            _emit_progress(progress, 2, 3, "running node realm_export")
            proc = subprocess.run(
                [node, export_js, tmp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
        if proc.returncode != 0:
            return None
        try:
            data = json.loads((proc.stdout or b"").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass
        try:
            _prune_old_dumps(cache_dir, keep_last)
        except Exception:
            pass
        _emit_progress(progress, 3, 3, "realm export done")
        return data
    except Exception:
        return None
