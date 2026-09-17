"""Background job registry: scans and online-checks run off-thread with progress."""
from __future__ import annotations

import itertools
import threading
import time
import traceback
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    kind: str  # "scan" | "online"
    state: str = "queued"  # queued | running | done | error
    done: int = 0
    total: int = 0
    error: str = ""
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("finished_at", None)
        return d


class JobRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._counter = itertools.count(1)

    def create(self, kind: str, total: int = 0) -> Job:
        with self._lock:
            jid = f"{kind}-{next(self._counter)}"
            job = Job(id=jid, kind=kind, total=total)
            self._jobs[jid] = job
            return job

    def get(self, jid: str) -> Job | None:
        with self._lock:
            return self._jobs.get(jid)

    def active(self) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()
                    if j.state in ("queued", "running")]

    def run_background(self, job: Job, fn) -> None:
        """Run fn(job) on a daemon thread; captures state + errors on the job."""
        def _wrap():
            job.state = "running"
            try:
                fn(job)
                job.state = "done"
            except Exception as e:  # keep serving; surface via jobs endpoint
                job.state = "error"
                job.error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            finally:
                job.finished_at = time.time()
        threading.Thread(target=_wrap, daemon=True).start()
