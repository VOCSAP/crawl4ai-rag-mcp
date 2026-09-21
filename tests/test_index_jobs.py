"""Tests for the asynchronous indexing job store.

Runnable without pytest (``python tests/test_index_jobs.py``) so it can run
inside the mcp-crawl4ai container, which ships no test dependencies. Exercises
the real index_jobs table against the configured Postgres, not a fake.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils


def _purge(job_id):
    with utils._pool_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM index_jobs WHERE id = %s", (job_id,))
        conn.commit()


def test_a_new_job_starts_queued_with_its_total():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=37)
    try:
        job = utils.get_index_job(job_id)
        assert job is not None, f"job {job_id} was created but reads back as None"
        assert job["state"] == "queued", f"expected state queued, got {job['state']!r}"
        assert job["total"] == 37, f"expected total 37, got {job['total']!r}"
        assert job["done"] == 0
        assert job["failed"] == 0
    finally:
        _purge(job_id)


def test_starting_a_job_marks_it_running_and_opens_a_heartbeat():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=3)
    try:
        assert utils.get_index_job(job_id)["heartbeat_at"] is None
        utils.start_index_job(job_id)
        job = utils.get_index_job(job_id)
        assert job["state"] == "running", f"expected running, got {job['state']!r}"
        assert job["started_at"] is not None
        assert job["heartbeat_at"] is not None, "a running job with no heartbeat reads as lost"
    finally:
        _purge(job_id)


def test_progress_advances_the_counters_and_touches_the_heartbeat():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=3)
    try:
        utils.start_index_job(job_id)
        first = utils.get_index_job(job_id)["heartbeat_at"]
        utils.bump_index_job(job_id, done_delta=1)
        utils.bump_index_job(job_id, failed_delta=1)
        job = utils.get_index_job(job_id)
        assert job["done"] == 1, f"expected done 1, got {job['done']!r}"
        assert job["failed"] == 1, f"expected failed 1, got {job['failed']!r}"
        # Each helper commits its own transaction, so now() differs between them.
        assert job["heartbeat_at"] > first, "progress did not touch the heartbeat"
    finally:
        _purge(job_id)


def test_finishing_a_job_marks_it_done():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=1)
    try:
        utils.start_index_job(job_id)
        utils.finish_index_job(job_id)
        job = utils.get_index_job(job_id)
        assert job["state"] == "done", f"expected done, got {job['state']!r}"
        assert job["finished_at"] is not None
        assert job["error"] is None
    finally:
        _purge(job_id)


def test_finishing_a_job_with_an_error_marks_it_failed():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=1)
    try:
        utils.start_index_job(job_id)
        utils.finish_index_job(job_id, error="ollama unreachable")
        job = utils.get_index_job(job_id)
        assert job["state"] == "failed", f"expected failed, got {job['state']!r}"
        assert job["error"] == "ollama unreachable"
        assert job["finished_at"] is not None
    finally:
        _purge(job_id)


def _age_heartbeat(job_id, seconds):
    with utils._pool_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE index_jobs SET heartbeat_at = now() - make_interval(secs => %s) WHERE id = %s",
                (seconds, job_id),
            )
        conn.commit()


def test_a_running_job_with_a_stale_heartbeat_reads_as_lost():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=5)
    try:
        utils.start_index_job(job_id)
        _age_heartbeat(job_id, 10 * 60)
        job = utils.get_index_job(job_id)
        assert job["state"] == "lost", (
            f"expected lost, got {job['state']!r}: a job killed by OOM cannot write "
            "its own final state, so a follower would wait forever"
        )
    finally:
        _purge(job_id)


def test_a_finished_job_with_a_stale_heartbeat_stays_done():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=1)
    try:
        utils.start_index_job(job_id)
        utils.finish_index_job(job_id)
        _age_heartbeat(job_id, 10 * 60)
        job = utils.get_index_job(job_id)
        assert job["state"] == "done", f"a terminal job must not decay into {job['state']!r}"
    finally:
        _purge(job_id)


def test_startup_abandons_jobs_left_behind_by_a_dead_process():
    """The work to run lives in memory, so a job that outlives its process has
    nobody left to run it. A queued job is never derived as lost either, so
    without this it would sit there forever and its follower would never be
    told."""
    utils.ensure_index_jobs_table()
    queued = utils.create_index_job(total=1)
    running = utils.create_index_job(total=1)
    finished = utils.create_index_job(total=1)
    try:
        utils.start_index_job(running)
        utils.finish_index_job(finished)

        utils.abandon_orphaned_index_jobs()

        assert utils.get_index_job(queued)["state"] == "failed", "the queued job was left stranded"
        assert utils.get_index_job(running)["state"] == "failed", "the running job was left stranded"
        assert utils.get_index_job(finished)["state"] == "done", "a terminal job was rewritten"
    finally:
        for job_id in (queued, running, finished):
            _purge(job_id)


TESTS = [
    test_a_new_job_starts_queued_with_its_total,
    test_starting_a_job_marks_it_running_and_opens_a_heartbeat,
    test_progress_advances_the_counters_and_touches_the_heartbeat,
    test_finishing_a_job_marks_it_done,
    test_finishing_a_job_with_an_error_marks_it_failed,
    test_a_running_job_with_a_stale_heartbeat_reads_as_lost,
    test_a_finished_job_with_a_stale_heartbeat_stays_done,
    test_startup_abandons_jobs_left_behind_by_a_dead_process,
]


if __name__ == "__main__":
    failures = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
