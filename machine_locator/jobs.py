"""Background jobs for work started from the web UI.

A metro-wide Overpass scan takes minutes. Rather than holding an HTTP request
open for that, the UI starts a job, gets an id back immediately, and polls for
progress. Each job runs in its own thread with its own SQLite connection --
connections are not shareable across threads, and quietly sharing one is a
classic source of "database is locked".
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .config import Settings
from .db import Database
from .models import utcnow

# fn(db, report) -> result dict; report(message, progress=None, total=None)
JobFn = Callable[[Database, Callable[..., None]], Dict[str, Any]]


class JobBusy(RuntimeError):
    """Raised when a job is asked for while another is already running."""


@dataclass
class JobHandle:
    id: int
    kind: str


class JobRunner:
    """Runs one job at a time and records its progress in the database."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._threads: Dict[int, threading.Thread] = {}

    def start(self, db: Database, kind: str, fn: JobFn, message: str = "Starting...") -> JobHandle:
        """Queue a job. Raises JobBusy if one is already in flight.

        Serialising jobs is deliberate: two concurrent Overpass scans would
        double the load on a free public API for no benefit.
        """
        with self._lock:
            active = db.active_job()
            if active:
                raise JobBusy(
                    f"A {active['kind']} job is already running. "
                    "Wait for it to finish, then try again."
                )
            job_id = db.create_job(kind, message)

        thread = threading.Thread(
            target=self._run, args=(job_id, fn), name=f"job-{job_id}-{kind}", daemon=True
        )
        self._threads[job_id] = thread
        thread.start()
        return JobHandle(id=job_id, kind=kind)

    def _run(self, job_id: int, fn: JobFn) -> None:
        # A fresh connection: this is a different thread from the request that
        # started the job.
        db = Database(self.settings.db_path)
        try:
            db.update_job(job_id, status="running", message="Working...")

            def report(message: str, progress: Optional[int] = None,
                       total: Optional[int] = None) -> None:
                fields: Dict[str, Any] = {"message": message}
                if progress is not None:
                    fields["progress"] = progress
                if total is not None:
                    fields["total"] = total
                db.update_job(job_id, **fields)

            result = fn(db, report) or {}
            db.update_job(
                job_id, status="done", message=result.get("summary", "Finished"),
                result=result, finished_at=utcnow(),
            )
        except Exception as exc:
            db.update_job(
                job_id,
                status="failed",
                message=f"{type(exc).__name__}",
                error=f"{exc}\n\n{traceback.format_exc(limit=4)}",
                finished_at=utcnow(),
            )
        finally:
            db.close()
            self._threads.pop(job_id, None)

    def is_running(self, job_id: int) -> bool:
        thread = self._threads.get(job_id)
        return bool(thread and thread.is_alive())
