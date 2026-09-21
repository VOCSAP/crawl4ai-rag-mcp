"""Tests for the background indexing worker.

Runnable without pytest (``python tests/test_index_worker.py``). The indexing
work itself is injected, so these tests need Postgres but neither Ollama nor a
browser.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils
import crawl4ai_mcp as mod


def _purge(job_id):
    with utils._pool_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM index_jobs WHERE id = %s", (job_id,))
        conn.commit()


BLOCKING_WORK_SECONDS = 1.0
# Well above the few milliseconds the synchronous job bookkeeping costs, and
# well below the second the injected work sleeps.
MAX_TOLERATED_STALL = 0.5


def test_running_a_job_leaves_the_event_loop_free():
    """The whole point of the job model: a 19-minute pipeline must not freeze
    the server. Called inline, the blocking work starves every other coroutine,
    which is what makes /health time out today.

    The measurement is the widest gap between two wake-ups, not a count: a
    coroutine looping a fixed number of times still completes all its laps
    after the loop unblocks, so counting laps cannot see the stall.
    """
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=1)
    gaps = []

    def _blocking_work(_job_id):
        time.sleep(BLOCKING_WORK_SECONDS)

    async def _watch(stop):
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    async def _both():
        stop = asyncio.Event()
        watcher = asyncio.create_task(_watch(stop))
        await asyncio.sleep(0)  # let the watcher take its first lap
        await mod._run_index_job(job_id, _blocking_work)
        stop.set()
        await watcher

    try:
        asyncio.run(_both())
        assert gaps, "the watcher never ran, so the measurement proves nothing"
        assert max(gaps) < MAX_TOLERATED_STALL, (
            f"the event loop stalled for {max(gaps):.2f}s while a "
            f"{BLOCKING_WORK_SECONDS}s job ran: the blocking work is still on the loop"
        )
    finally:
        _purge(job_id)


def test_a_completed_job_ends_done():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=1)
    try:
        asyncio.run(mod._run_index_job(job_id, lambda _job_id: None))
        job = utils.get_index_job(job_id)
        assert job["state"] == "done", f"expected done, got {job['state']!r}"
        assert job["finished_at"] is not None
    finally:
        _purge(job_id)


def test_work_that_raises_ends_the_job_failed_with_its_message():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=1)

    def _boom(_job_id):
        raise RuntimeError("ollama unreachable")

    try:
        asyncio.run(mod._run_index_job(job_id, _boom))
        job = utils.get_index_job(job_id)
        assert job["state"] == "failed", f"expected failed, got {job['state']!r}"
        assert "ollama unreachable" in (job["error"] or ""), (
            f"the cause is lost, error reads {job['error']!r}"
        )
        assert job["finished_at"] is not None, "a failed job must still be terminal"
    finally:
        _purge(job_id)


def test_a_long_job_keeps_its_heartbeat_fresh_while_it_works():
    """A heartbeat says the process is alive, not that work progressed. Without
    one beating on its own, a healthy 19-minute job goes silent between two
    step boundaries and reads as lost long before it ends."""
    utils.ensure_index_jobs_table()
    os.environ["INDEX_JOB_STALE_SECONDS"] = "1"
    os.environ["INDEX_JOB_HEARTBEAT_SECONDS"] = "0.2"
    job_id = utils.create_index_job(total=1)
    seen = []

    def _long_quiet_work(_job_id):
        # Reports nothing, exactly like add_documents_to_db does today.
        time.sleep(2.5)

    async def _watch():
        for _ in range(12):
            await asyncio.sleep(0.2)
            seen.append(utils.get_index_job(job_id)["state"])

    async def _both():
        await asyncio.gather(mod._run_index_job(job_id, _long_quiet_work), _watch())

    try:
        asyncio.run(_both())
        assert "lost" not in seen, (
            f"a healthy job was declared lost while still working: {seen}"
        )
        assert utils.get_index_job(job_id)["state"] == "done"
    finally:
        os.environ.pop("INDEX_JOB_STALE_SECONDS", None)
        os.environ.pop("INDEX_JOB_HEARTBEAT_SECONDS", None)
        _purge(job_id)


def test_progress_advances_chunk_by_chunk_not_in_one_jump():
    """A follower should see 12/37 then 13/37 at the real pace of the work, not
    0/37 for nineteen minutes and then 37/37."""
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=4)
    seen = []
    real = mod.add_documents_to_db

    def _fake_add(*a, on_chunk=None, **k):
        for _ in range(4):
            if on_chunk:
                on_chunk(False)
            seen.append(utils.get_index_job(job_id)["done"])
        return 0

    mod.add_documents_to_db = _fake_add
    try:
        mod._index_crawl_payload(job_id, {}, {}, ["u"], [0], ["c"] * 4, [{}] * 4, {}, [], 20)
        assert seen == [1, 2, 3, 4], f"done went {seen}, so progress is not per chunk"
        assert utils.get_index_job(job_id)["done"] == 4, "the total was counted twice"
    finally:
        mod.add_documents_to_db = real
        _purge(job_id)


def test_chunks_that_lost_their_context_are_counted_on_the_job():
    """A chunk whose LLM context call fails is still indexed, as its raw self.
    Silently reporting zero failures would hide a degraded index."""
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=5)
    real = mod.add_documents_to_db

    def _fake_add(*a, on_chunk=None, **k):
        for i in range(5):
            if on_chunk:
                on_chunk(i < 2)  # the first two lost their context
        return 2

    mod.add_documents_to_db = _fake_add
    try:
        mod._index_crawl_payload(job_id, {}, {}, ["u"], [0], ["c"] * 5, [{}] * 5, {}, [], 20)
        job = utils.get_index_job(job_id)
        assert job["failed"] == 2, f"expected 2 degraded chunks, job reports {job['failed']}"
        assert job["done"] == 5, f"expected 5 indexed chunks, job reports {job['done']}"
    finally:
        mod.add_documents_to_db = real
        _purge(job_id)


TESTS = [
    test_running_a_job_leaves_the_event_loop_free,
    test_progress_advances_chunk_by_chunk_not_in_one_jump,
    test_chunks_that_lost_their_context_are_counted_on_the_job,
    test_a_long_job_keeps_its_heartbeat_fresh_while_it_works,
    test_a_completed_job_ends_done,
    test_work_that_raises_ends_the_job_failed_with_its_message,
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
