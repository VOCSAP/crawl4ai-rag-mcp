"""Tests that scrape_urls hands back a job instead of waiting for indexing.

Runnable without pytest (``python tests/test_scrape_returns_job.py``). The crawl
and the indexing work are both stubbed: what is under test is the wiring, not
the pipeline.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils
import crawl4ai_mcp as mod

PAGE = "# Title\n\n" + ("body text that is long enough to chunk. " * 200)


def _purge(job_id):
    with utils._pool_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM index_jobs WHERE id = %s", (job_id,))
        conn.commit()


async def _fake_crawl_batch(crawler, urls, max_concurrent=10):
    return [{"url": u, "markdown": PAGE, "links": {"internal": [], "external": []}} for u in urls]


def _run_scrape():
    """Drive _process_multiple_urls with the crawl and the indexing stubbed."""
    indexed = []
    real_crawl, real_index = mod.crawl_batch, mod._index_crawl_payload
    mod.crawl_batch = _fake_crawl_batch
    mod._index_crawl_payload = lambda *a, **k: indexed.append(1) or 0
    try:
        raw = asyncio.run(
            mod._process_multiple_urls(None, ["https://example.test/doc"], 10, 20, time.time())
        )
    finally:
        mod.crawl_batch, mod._index_crawl_payload = real_crawl, real_index
    return json.loads(raw), indexed


def test_with_contextual_embeddings_the_caller_gets_a_job_to_follow():
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    body, _ = _run_scrape()
    job_id = body.get("job_id")
    try:
        assert job_id, f"no job_id in the answer: {sorted(body)}"
        assert body.get("indexing") in ("queued", "running"), (
            f"indexing reads {body.get('indexing')!r}"
        )
        follow = body.get("follow", "")
        assert follow.startswith("curl "), f"follow is {follow!r}, not a runnable command"
        assert job_id in follow, "the follow command does not point at this job"
        assert utils.get_index_job(job_id) is not None, "the job id does not exist in the store"
    finally:
        if job_id:
            _purge(job_id)


def test_without_contextual_embeddings_nothing_changes_for_the_caller():
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "false"
    body, indexed = _run_scrape()
    for field in ("job_id", "indexing", "follow"):
        assert field not in body, f"{field} leaked into the cheap path: {body[field]!r}"
    assert indexed == [1], "the indexing did not run inline on the cheap path"
    assert body["success"] is True


TESTS = [
    test_with_contextual_embeddings_the_caller_gets_a_job_to_follow,
    test_without_contextual_embeddings_nothing_changes_for_the_caller,
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
