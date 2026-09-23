"""
In-memory background-job tracker so the frontend can show LIVE progress
for the slow, combinatorial searches (waiver pickups, waiver plan, trade
finder, data refresh) instead of blocking on one giant request.

This is a single-process, single-user local tool (same as the old
Streamlit app) - a plain dict guarded by a lock is enough; nothing here
needs to survive a server restart, and there's no multi-worker deployment
to coordinate across.

Usage:
    job_id = jobs.start(lambda job: job.set_result(do_the_work(job.report)))
    ...
    jobs.get(job_id).to_dict()   # poll this from the frontend
"""

import threading
import traceback
import uuid
from typing import Callable, Optional

_jobs: dict = {}
_lock = threading.Lock()

# Old jobs are trimmed on each `start()` call so a long-running server
# doesn't leak memory across many searches in one session.
_MAX_JOBS = 200


class Job:
    def __init__(self, job_id: str):
        self.id = job_id
        self.status = "running"  # running | done | error
        self.progress = {"current": 0, "total": None, "message": ""}
        self.result = None
        self.error: Optional[str] = None
        self.log: list = []
        self._lock = threading.Lock()

    def report(self, current, total=None, message=None):
        """Compatible with both call shapes already used in this codebase:
        trades.suggest_trades calls progress_callback(considered, total);
        waiver's new callbacks add an optional message. Extra positional
        args beyond these three are ignored rather than raising, so this
        stays a safe drop-in for either."""
        with self._lock:
            self.progress = {
                "current": current,
                "total": total,
                "message": message if message is not None else self.progress.get("message", ""),
            }

    def log_line(self, line: str):
        with self._lock:
            self.log.append(line)
            if len(self.log) > 300:
                self.log = self.log[-300:]

    def set_result(self, result):
        self.result = result

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "progress": dict(self.progress),
                "result": self.result,
                "error": self.error,
                "log": list(self.log[-80:]),
            }


def start(fn: Callable[[Job], None]) -> str:
    """Runs fn(job) on a background thread and returns the job id right
    away. fn should call job.set_result(...) with whatever the frontend
    should eventually see, and can call job.report(...)/job.log_line(...)
    as it goes. Any exception fn raises is caught and surfaced as
    job.error rather than being lost on a background thread."""
    job = Job(str(uuid.uuid4()))

    with _lock:
        _jobs[job.id] = job
        if len(_jobs) > _MAX_JOBS:
            for old_id in list(_jobs.keys())[: len(_jobs) - _MAX_JOBS]:
                _jobs.pop(old_id, None)

    def _run():
        try:
            fn(job)
            job.status = "done"
        except Exception as e:  # noqa: BLE001 - a background thread has no other way to report this
            job.error = str(e) or repr(e)
            job.status = "error"
            job.log_line(traceback.format_exc())

    threading.Thread(target=_run, daemon=True, name=f"job-{job.id[:8]}").start()
    return job.id


def get(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)