"""Tests for the decision to index inline or in the background.

Runnable without pytest (``python tests/test_index_dispatch.py``).
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


def test_without_contextual_embeddings_the_work_is_done_before_returning():
    """The cheap path must keep today's semantics: when the caller gets its
    answer, the indexing is over and no job exists to follow."""
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "false"
    done = []
    job_id = asyncio.run(mod._dispatch_indexing(lambda _j: done.append(1), total=3))
    assert job_id is None, f"a job was created for the inline path: {job_id!r}"
    assert done == [1], "the work had not run by the time the caller was answered"


def test_with_contextual_embeddings_a_job_id_comes_back_before_the_work_ends():
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    started = []

    def _slow(_job_id):
        started.append(time.monotonic())
        time.sleep(0.5)

    async def _scenario():
        job_id = await mod._dispatch_indexing(_slow, total=3)
        # The caller is answered here; the work is still running.
        state_at_return = utils.get_index_job(job_id)["state"]
        for _ in range(100):
            await asyncio.sleep(0.05)
            if utils.get_index_job(job_id)["state"] == "done":
                break
        return job_id, state_at_return

    job_id, state_at_return = asyncio.run(_scenario())
    try:
        assert job_id is not None, "no job id came back on the deferred path"
        assert state_at_return != "done", (
            "the work finished before the caller was answered, so nothing was deferred"
        )
        assert utils.get_index_job(job_id)["state"] == "done", "the deferred work never ran"
        assert started, "the work was never called"
    finally:
        _purge(job_id)


def test_a_second_job_waits_when_only_one_slot_is_open():
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    os.environ["INDEX_JOB_CONCURRENCY"] = "1"
    ids = []

    async def _scenario():
        first = await mod._dispatch_indexing(lambda _j: time.sleep(0.6), total=1)
        second = await mod._dispatch_indexing(lambda _j: None, total=1)
        ids.extend([first, second])
        await asyncio.sleep(0.2)
        return utils.get_index_job(second)["state"]

    try:
        state = asyncio.run(_scenario())
        assert state == "queued", (
            f"the second job is {state!r} while the only slot is taken: "
            "INDEX_JOB_CONCURRENCY is not enforced"
        )
    finally:
        for job_id in ids:
            if job_id:
                _purge(job_id)


def test_a_caller_that_reads_its_own_writes_indexes_inline():
    """smart_crawl_url can run RAG queries over what it just crawled. Deferring
    there would query an index that is not filled yet."""
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    done = []
    job_id = asyncio.run(
        mod._dispatch_indexing(lambda _j: done.append(1), total=3, allow_defer=False)
    )
    assert job_id is None, f"a job was created despite allow_defer=False: {job_id!r}"
    assert done == [1], "the work had not run by the time the caller was answered"


def test_the_follow_command_targets_the_configured_base_url():
    os.environ["INDEX_JOB_FOLLOW_BASE_URL"] = "https://rag.example.test"
    cmd = mod._follow_command("j-1")
    assert "https://rag.example.test/jobs/j-1/stream" in cmd, f"got {cmd!r}"
    assert cmd.startswith("curl "), f"the caller is handed {cmd!r}, not a runnable command"


TESTS = [
    test_without_contextual_embeddings_the_work_is_done_before_returning,
    test_with_contextual_embeddings_a_job_id_comes_back_before_the_work_ends,
    test_a_second_job_waits_when_only_one_slot_is_open,
    test_a_caller_that_reads_its_own_writes_indexes_inline,
    test_the_follow_command_targets_the_configured_base_url,
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
