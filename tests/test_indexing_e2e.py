"""End-to-end check of the real document writer.

Runnable without pytest (``python tests/test_indexing_e2e.py``). Unlike the
other job tests, this one calls the production add_documents_to_db with real
bge-m3 embeddings against the real Postgres. Only the LLM context call is
stubbed, so no model beyond the loaded embedding one is touched.

It exists because every other per-chunk test stubs add_documents_to_db, which
proves the caller passes a callback but never that the writer calls it.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils

PROBE_URL = "https://probe.invalid/per-chunk-progress"


def _purge_probe():
    with utils._pool_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crawled_pages WHERE url = %s", (PROBE_URL,))
            cur.execute("DELETE FROM sources WHERE source_id = %s", ("probe.invalid",))
        conn.commit()


def test_the_real_writer_reports_every_chunk_it_stores():
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    chunks = ["alpha text for probe", "beta text for probe", "gamma text for probe"]
    seen = []
    real_context = utils.generate_contextual_embedding
    # Stubbed so the probe needs no chat model, only the loaded embedder.
    utils.generate_contextual_embedding = lambda _doc, chunk: (chunk, True)
    try:
        utils.update_source_info("probe.invalid", "progress probe", 0)
        degraded = utils.add_documents_to_db(
            [PROBE_URL] * 3,
            [0, 1, 2],
            chunks,
            [{"source": "probe.invalid"} for _ in chunks],
            {PROBE_URL: " ".join(chunks)},
            batch_size=20,
            on_chunk=lambda was_degraded: seen.append(was_degraded),
        )
        assert len(seen) == 3, (
            f"the production writer fired the callback {len(seen)} times for 3 chunks: "
            "per-chunk progress is wired in the caller but not in the writer"
        )
        assert degraded == 0, f"expected no degraded chunk, got {degraded}"

        with utils._pool_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM crawled_pages WHERE url = %s", (PROBE_URL,))
                stored = cur.fetchone()[0]
        assert stored == 3, f"expected 3 rows stored with real embeddings, found {stored}"
    finally:
        utils.generate_contextual_embedding = real_context
        _purge_probe()


def test_the_real_writer_counts_chunks_that_lost_their_context():
    """Only the success branch was exercised against real code. A chunk whose
    context call raises is still stored, as its raw self, and must be counted."""
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    chunks = ["delta text for probe", "epsilon text for probe"]
    seen = []
    real_context = utils.generate_contextual_embedding

    def _boom(_doc, _chunk):
        raise RuntimeError("context model unreachable")

    utils.generate_contextual_embedding = _boom
    try:
        utils.update_source_info("probe.invalid", "progress probe", 0)
        degraded = utils.add_documents_to_db(
            [PROBE_URL] * 2,
            [0, 1],
            chunks,
            [{"source": "probe.invalid"} for _ in chunks],
            {PROBE_URL: " ".join(chunks)},
            batch_size=20,
            on_chunk=lambda was_degraded: seen.append(was_degraded),
        )
        assert degraded == 2, f"expected 2 degraded chunks, writer reported {degraded}"
        assert seen == [True, True], f"the callback reported {seen}"
    finally:
        utils.generate_contextual_embedding = real_context
        _purge_probe()


def test_the_callback_survives_the_batch_boundary():
    """Chunks are written in batches. A counter reset per batch would go
    unnoticed with a single batch, which is all the other tests use."""
    os.environ["USE_CONTEXTUAL_EMBEDDINGS"] = "true"
    chunks = [f"chunk number {i} for the batch probe" for i in range(5)]
    seen = []
    real_context = utils.generate_contextual_embedding
    utils.generate_contextual_embedding = lambda _doc, chunk: (chunk, True)
    try:
        utils.update_source_info("probe.invalid", "progress probe", 0)
        utils.add_documents_to_db(
            [PROBE_URL] * 5,
            list(range(5)),
            chunks,
            [{"source": "probe.invalid"} for _ in chunks],
            {PROBE_URL: " ".join(chunks)},
            batch_size=2,
            on_chunk=lambda was_degraded: seen.append(was_degraded),
        )
        assert len(seen) == 5, (
            f"5 chunks over 3 batches of 2 produced {len(seen)} callbacks: "
            "progress is lost at a batch boundary"
        )
    finally:
        utils.generate_contextual_embedding = real_context
        _purge_probe()


TESTS = [
    test_the_real_writer_reports_every_chunk_it_stores,
    test_the_real_writer_counts_chunks_that_lost_their_context,
    test_the_callback_survives_the_batch_boundary,
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
