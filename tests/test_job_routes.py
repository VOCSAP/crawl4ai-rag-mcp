"""Tests for the HTTP routes that expose an indexing job.

Runnable without pytest (``python tests/test_job_routes.py``). Drives the real
Starlette app through TestClient, so a route that is never registered fails
here rather than in production.
"""
import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Read at import time by the stream handler, so set them before importing.
os.environ.setdefault("INDEX_JOB_STREAM_POLL_SECONDS", "0.05")
os.environ.setdefault("INDEX_JOB_STREAM_KEEPALIVE_SECONDS", "0.2")

import utils
import crawl4ai_mcp as mod
from starlette.testclient import TestClient

CLIENT = TestClient(mod.mcp.streamable_http_app())

JOB_SECONDS = 1.0


def _purge(job_id):
    with utils._pool_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM index_jobs WHERE id = %s", (job_id,))
        conn.commit()


def test_reading_an_unknown_job_is_404():
    r = CLIENT.get("/jobs/11111111-2222-3333-4444-555555555555")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"


def test_reading_a_job_returns_its_counters():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=37)
    try:
        utils.start_index_job(job_id)
        utils.bump_index_job(job_id, done_delta=12)
        r = CLIENT.get(f"/jobs/{job_id}")
        assert r.status_code == 200, f"expected 200, got {r.status_code}"
        body = r.json()
        assert body["state"] == "running", f"got state {body['state']!r}"
        assert body["done"] == 12
        assert body["total"] == 37
        assert body["id"] == job_id
    finally:
        _purge(job_id)


def test_following_an_unknown_job_is_404_not_an_empty_stream():
    r = CLIENT.get("/jobs/11111111-2222-3333-4444-555555555555/stream")
    assert r.status_code == 404, (
        f"expected 404, got {r.status_code}: a follower launched on a wrong id "
        "would otherwise wait forever"
    )


def test_the_stream_closes_once_the_job_is_terminal():
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=1)
    try:
        utils.start_index_job(job_id)
        utils.finish_index_job(job_id)
        started = time.monotonic()
        with CLIENT.stream("GET", f"/jobs/{job_id}/stream") as r:
            lines = [ln for ln in r.iter_lines() if ln.strip()]
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"the stream stayed open {elapsed:.1f}s on a finished job"
        assert lines, "the stream closed without reporting the final state"
        assert json.loads(lines[-1])["state"] == "done", f"last line was {lines[-1]!r}"
    finally:
        _purge(job_id)


def test_the_stream_keeps_alive_while_nothing_progresses():
    """nginx closes a proxied connection after 60s without a byte, so silence
    is what would break the follow through NPM."""
    utils.ensure_index_jobs_table()
    job_id = utils.create_index_job(total=100)
    try:
        utils.start_index_job(job_id)
        # Nothing will progress; only the keep-alive can produce these lines.
        finisher = threading.Timer(1.5, utils.finish_index_job, args=(job_id,))
        finisher.start()
        try:
            with CLIENT.stream("GET", f"/jobs/{job_id}/stream") as r:
                lines = [ln for ln in r.iter_lines() if ln.strip()]
        finally:
            finisher.cancel()
        assert len(lines) >= 3, (
            f"only {len(lines)} lines in 1.5s at a 0.2s keep-alive: a silent "
            "stream gets cut by nginx long before a 19-minute job ends"
        )
    finally:
        _purge(job_id)


def test_health_answers_promptly_while_a_job_is_running():
    """The contract's own wording, over the real route.

    Two things this test got wrong before and that its negative control
    caught. TestClient serves the app in its own event loop on another thread,
    so a job blocking this one went unnoticed: the request has to share the
    loop, hence ASGITransport here. And the status code proves nothing, since a
    blocked loop still answers 200 once it unblocks. Latency is the signal.
    """
    from httpx import AsyncClient, ASGITransport

    utils.ensure_index_jobs_table()
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    codes = []

    async def _scenario():
        # Timed as one block, job start included: a task created here does not
        # run until the next await, so timing only the requests would measure
        # the loop after the stall rather than during it.
        started = time.monotonic()
        job_id = await mod._dispatch_indexing(lambda _j: time.sleep(JOB_SECONDS), total=1)
        # Hands control to the scheduler so the job task actually starts. Without
        # it the task never runs during the measurement: an ASGITransport request
        # is coroutines all the way down with no real I/O, so it never yields.
        await asyncio.sleep(0.05)
        transport = ASGITransport(app=mod.mcp.streamable_http_app())
        async with AsyncClient(transport=transport, base_url="http://probe") as client:
            for _ in range(5):
                codes.append((await client.get("/health")).status_code)
        return job_id, time.monotonic() - started

    job_id, elapsed = asyncio.run(_scenario())
    try:
        assert codes == [200] * 5, f"/health answered {codes} while a job ran"
        assert elapsed < JOB_SECONDS / 2, (
            f"five /health calls took {elapsed:.2f}s against a {JOB_SECONDS}s job: "
            "the indexing is back on the event loop"
        )
    finally:
        _purge(job_id)


TESTS = [
    test_health_answers_promptly_while_a_job_is_running,
    test_reading_an_unknown_job_is_404,
    test_reading_a_job_returns_its_counters,
    test_following_an_unknown_job_is_404_not_an_empty_stream,
    test_the_stream_closes_once_the_job_is_terminal,
    test_the_stream_keeps_alive_while_nothing_progresses,
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
