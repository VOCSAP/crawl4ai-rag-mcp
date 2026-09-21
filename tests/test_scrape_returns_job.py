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


def _count_jobs():
    with utils._pool_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM index_jobs")
            return cur.fetchone()[0]


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


def _run_smart_crawl(query=None, trace=None):
    """Drive smart_crawl_url on a plain page, crawl and indexing stubbed."""
    real_single, real_index = mod.crawl_markdown_file, mod._index_crawl_payload

    async def _fake_single(crawler, url):
        return [{"url": url, "markdown": PAGE}]

    def _fake_index(*a, **k):
        if trace is not None:
            trace.append("indexed")
        return 0

    mod.crawl_markdown_file = _fake_single
    mod._index_crawl_payload = _fake_index

    class _Ctx:
        class request_context:
            class lifespan_context:
                @staticmethod
                async def get_crawler():
                    return None

    try:
        raw = asyncio.run(mod.smart_crawl_url(_Ctx, "https://example.test/page.txt", query=query))
    finally:
        mod.crawl_markdown_file, mod._index_crawl_payload = real_single, real_index
    return json.loads(raw)


def test_smart_crawl_still_answers_after_the_indexing_moved_out():
    """smart_crawl_url built its own reply from variables that lived in the
    indexing block. Moving that block out must not leave a dangling name."""
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    body = _run_smart_crawl()
    job_id = body.get("job_id")
    try:
        assert body.get("success") is True, f"smart_crawl_url failed: {body}"
        assert "code_examples_stored" in body, f"field dropped from the answer: {sorted(body)}"
    finally:
        if job_id:
            _purge(job_id)


def test_smart_crawl_in_query_mode_indexes_before_it_searches():
    """Asserting the absence of job fields would prove nothing: the query reply
    never carries them. What matters is that the indexing already ran when the
    call returns, since the query reads it back."""
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    utils.ensure_index_jobs_table()
    trace = []
    real_rag = mod.perform_rag_query

    async def _spy_rag(*a, **k):
        trace.append("searched")
        return json.dumps({"success": True, "results": []})

    mod.perform_rag_query = _spy_rag
    before = _count_jobs()
    try:
        body = _run_smart_crawl(query="anything", trace=trace)
    finally:
        mod.perform_rag_query = real_rag

    assert body.get("success") is True, f"smart_crawl_url failed in query mode: {body}"
    assert "indexed" in trace, "the indexing never ran on the query path"
    # The decisive check. Asserting the order would race: a deferred task can
    # still happen to run before the search. A job row cannot exist at all if
    # the indexing stayed inline.
    assert _count_jobs() == before, (
        "query mode created a background job, so the search reads an index that "
        "is not filled yet"
    )


TESTS = [
    test_with_contextual_embeddings_the_caller_gets_a_job_to_follow,
    test_smart_crawl_still_answers_after_the_indexing_moved_out,
    test_smart_crawl_in_query_mode_indexes_before_it_searches,
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
