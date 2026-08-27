import time

import pytest

from machine_locator.jobs import JobBusy, JobRunner


def wait_for(db, job_id, timeout=6.0):
    """Poll until the job leaves the running state, like the UI does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = db.get_job(job_id)
        if job and job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def test_job_runs_and_records_its_result(settings, db):
    runner = JobRunner(settings)
    handle = runner.start(db, "demo", lambda database, report: {
        "summary": "all done", "found": 7
    })
    job = wait_for(db, handle.id)
    assert job["status"] == "done"
    assert job["message"] == "all done"
    assert job["result"]["found"] == 7
    assert job["finished_at"]


def test_job_reports_progress_while_running(settings, db):
    runner = JobRunner(settings)

    def work(database, report):
        report("halfway", progress=5, total=10)
        return {"summary": "finished"}

    handle = runner.start(db, "demo", work)
    wait_for(db, handle.id)
    # Progress survives to the finished row, so a reload still shows the total.
    assert db.get_job(handle.id)["total"] == 10


def test_a_failing_job_is_recorded_not_raised(settings, db):
    runner = JobRunner(settings)

    def boom(database, report):
        raise ValueError("overpass exploded")

    handle = runner.start(db, "demo", boom)
    job = wait_for(db, handle.id)
    assert job["status"] == "failed"
    assert job["message"] == "ValueError"
    assert "overpass exploded" in job["error"]


def test_only_one_job_runs_at_a_time(settings, db):
    runner = JobRunner(settings)
    release = {"go": False}

    def slow(database, report):
        while not release["go"]:
            time.sleep(0.01)
        return {"summary": "done"}

    first = runner.start(db, "scan", slow)
    with pytest.raises(JobBusy, match="already running"):
        runner.start(db, "scan", lambda d, r: {})
    release["go"] = True
    wait_for(db, first.id)

    # Once it finishes, the next one is allowed.
    second = runner.start(db, "scan", lambda d, r: {"summary": "ok"})
    assert wait_for(db, second.id)["status"] == "done"


def test_job_uses_its_own_database_connection(settings, db):
    """Writes from the job thread must be visible to the request thread."""
    runner = JobRunner(settings)

    def write(database, report):
        assert database is not db  # a separate connection, not the caller's
        database.set_setting("written_by_job", "yes")
        return {"summary": "wrote"}

    handle = runner.start(db, "demo", write)
    wait_for(db, handle.id)
    assert db.get_setting("written_by_job") == "yes"


def test_active_job_is_visible_then_clears(settings, db):
    runner = JobRunner(settings)
    release = {"go": False}

    handle = runner.start(db, "scan", lambda d, r: (
        [time.sleep(0.01) for _ in iter(lambda: release["go"], True)], {"summary": "ok"}
    )[1])
    assert db.active_job()["id"] == handle.id
    release["go"] = True
    wait_for(db, handle.id)
    assert db.active_job() is None
