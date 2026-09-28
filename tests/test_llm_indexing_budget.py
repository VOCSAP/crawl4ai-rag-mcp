"""Behavioral tests for the shared LLM indexing budget."""
import concurrent.futures
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils


class _ChatResponse:
    def __init__(self):
        message = type("Message", (), {"content": "context"})()
        self.choices = [type("Choice", (), {"message": message})()]


class _SlowChatClient:
    def __init__(self, calls, delay=0.0):
        self._calls = calls
        self._delay = delay
        self.chat = self
        self.completions = self

    def create(self, **_kwargs):
        self._calls.append(time.monotonic())
        time.sleep(self._delay)
        return _ChatResponse()


class _Cursor:
    def __init__(self):
        self.executed = []

    def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self):
        self.cursor_instance = _Cursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def test_budget_refuses_expired_and_exhausted_acquisitions():
    expired = utils.LLMBudget(seconds=0, max_calls=5)
    assert not expired.acquire()
    assert expired.stop_reason == "budget_time"

    exhausted = utils.LLMBudget(seconds=60, max_calls=2)
    assert exhausted.acquire()
    assert exhausted.acquire()
    assert not exhausted.acquire()
    assert exhausted.stop_reason == "budget_calls"


def test_budget_allows_exactly_the_configured_calls_under_concurrency():
    budget = utils.LLMBudget(seconds=60, max_calls=7)
    barrier = threading.Barrier(20)

    def _acquire():
        barrier.wait()
        return budget.acquire()

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        accepted = sum(executor.map(lambda _i: _acquire(), range(20)))

    assert accepted == 7
    assert budget.calls == 7
    assert budget.stop_reason == "budget_calls"


def test_index_payload_starts_a_queued_budget_when_indexing_begins():
    import crawl4ai_mcp as mod

    budget = utils.LLMBudget(seconds=0.005, max_calls=1)
    time.sleep(0.03)

    mod._index_crawl_payload(None, {}, {}, [], [], [], [], {}, [], 20, budget=budget)

    assert budget.acquire(), "queue time exhausted the indexing budget before work began"


def test_one_budget_is_shared_by_source_context_and_code_summary_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_get_openai_client", lambda **_kwargs: _SlowChatClient(calls))
    budget = utils.LLMBudget(seconds=60, max_calls=2)

    assert utils.extract_source_summary("example.test", "source", budget=budget) == "context"
    contextual, succeeded = utils.generate_contextual_embedding("source", "chunk", budget=budget)
    assert succeeded
    assert contextual.endswith("chunk")
    assert utils.generate_code_example_summary("code", "before", "after", budget=budget) == (
        "Code example for demonstration purposes."
    )
    assert len(calls) == 2
    assert budget.stop_reason == "budget_calls"


def test_budget_counts_fallbacks_across_each_llm_stage(monkeypatch):
    monkeypatch.setenv("MODEL_CHOICE", "test-model")
    budget = utils.LLMBudget(seconds=60, max_calls=0)

    assert utils.extract_source_summary("example.test", "source", budget=budget) == "Content from example.test"
    contextual, succeeded = utils.generate_contextual_embedding("source", "chunk", budget=budget)
    assert contextual == "chunk"
    assert not succeeded
    assert utils.generate_code_example_summary("code", "before", "after", budget=budget) == (
        "Code example for demonstration purposes."
    )
    assert budget.calls == 0
    assert budget.fallbacks == 3


@pytest.mark.parametrize(
    "call_stage",
    [
        lambda budget: utils.extract_source_summary("example.test", "source", budget=budget),
        lambda budget: utils.generate_contextual_embedding("source", "chunk", budget=budget),
        lambda budget: utils.generate_code_example_summary("code", "before", "after", budget=budget),
    ],
    ids=["source-summary", "contextual-embedding", "code-example-summary"],
)
def test_llm_stage_skips_request_after_slow_client_setup_exhausts_budget(monkeypatch, call_stage):
    calls = []

    def slow_client_factory(**_kwargs):
        time.sleep(0.05)
        return _SlowChatClient(calls)

    monkeypatch.setattr(utils, "_get_openai_client", slow_client_factory)
    budget = utils.LLMBudget(seconds=0.01, max_calls=1)

    call_stage(budget)

    assert calls == []
    assert budget.calls == 0
    assert budget.stop_reason == "budget_time"


@pytest.mark.parametrize(
    "call_stage",
    [
        lambda budget: utils.extract_source_summary("example.test", "source", budget=budget),
        lambda budget: utils.generate_contextual_embedding("source", "chunk", budget=budget),
        lambda budget: utils.generate_code_example_summary("code", "before", "after", budget=budget),
    ],
    ids=["source-summary", "contextual-embedding", "code-example-summary"],
)
def test_llm_stage_starts_request_before_budget_deadline(monkeypatch, call_stage):
    calls = []

    def slow_client_factory(**_kwargs):
        time.sleep(0.01)
        return _SlowChatClient(calls)

    monkeypatch.setattr(utils, "_get_openai_client", slow_client_factory)
    budget_seconds = 0.1
    budget = utils.LLMBudget(seconds=budget_seconds, max_calls=1)
    deadline = time.monotonic() + budget_seconds

    call_stage(budget)

    assert calls
    assert all(call_started_at < deadline for call_started_at in calls)


def test_budget_stops_slow_context_calls_while_all_chunks_are_inserted(monkeypatch):
    calls = []
    stored = []
    monkeypatch.setattr(utils, "_get_openai_client", lambda **_kwargs: _SlowChatClient(calls, delay=0.5))
    monkeypatch.setattr(utils, "create_embeddings_batch", lambda texts: [[0.0] for _ in texts])
    monkeypatch.setattr(
        utils.psycopg2.extras,
        "execute_values",
        lambda _cursor, _sql, values, **_kwargs: stored.extend(values),
    )
    monkeypatch.setenv("USE_CONTEXTUAL_EMBEDDINGS", "true")
    monkeypatch.setenv("CONTEXTUAL_EMBEDDING_WORKERS", "2")

    chunks = [f"chunk {i}" for i in range(50)]
    budget = utils.LLMBudget(seconds=2, max_calls=50)
    started = time.monotonic()
    degraded = utils._add_documents_to_db_impl(
        _Connection(),
        ["https://example.test/page"] * len(chunks),
        list(range(len(chunks))),
        chunks,
        [{} for _ in chunks],
        {"https://example.test/page": "document"},
        batch_size=50,
        budget=budget,
    )
    elapsed = time.monotonic() - started

    max_calls_before_deadline = 2 * (int(2 / 0.5) + 1)
    assert elapsed <= 3.0, f"indexing took {elapsed:.2f}s despite a 2s LLM budget"
    assert len(calls) <= max_calls_before_deadline, (
        f"{len(calls)} LLM calls exceeded the time budget bound of {max_calls_before_deadline}"
    )
    assert len(stored) == len(chunks), "chunks after the budget were not indexed"
    assert degraded == len(chunks) - len(calls)


def test_cancelled_budget_indexes_chunks_raw_without_new_llm_calls(monkeypatch):
    calls = []
    stored = []
    monkeypatch.setattr(utils, "_get_openai_client", lambda **_kwargs: _SlowChatClient(calls))
    monkeypatch.setattr(utils, "create_embeddings_batch", lambda texts: [[0.0] for _ in texts])
    monkeypatch.setattr(
        utils.psycopg2.extras,
        "execute_values",
        lambda _cursor, _sql, values, **_kwargs: stored.extend(values),
    )
    monkeypatch.setenv("USE_CONTEXTUAL_EMBEDDINGS", "true")

    chunks = ["one", "two", "three"]
    budget = utils.LLMBudget(seconds=60, max_calls=60)
    budget.cancel()
    degraded = utils._add_documents_to_db_impl(
        _Connection(),
        ["https://example.test/page"] * len(chunks),
        list(range(len(chunks))),
        chunks,
        [{} for _ in chunks],
        {"https://example.test/page": "document"},
        batch_size=3,
        budget=budget,
    )

    assert calls == []
    assert len(stored) == len(chunks)
    assert degraded == len(chunks)


def test_index_payload_uses_one_budget_for_each_llm_stage(monkeypatch):
    import crawl4ai_mcp as mod

    calls = []
    seen_budgets = []
    monkeypatch.setattr(utils, "_get_openai_client", lambda **_kwargs: _SlowChatClient(calls))
    monkeypatch.setattr(mod, "update_source_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mod,
        "extract_code_blocks",
        lambda _markdown: [
            {"code": "code", "context_before": "before", "context_after": "after"},
            {"code": "code", "context_before": "before", "context_after": "after"},
        ],
    )
    monkeypatch.setattr(mod, "add_code_examples_to_db", lambda *_args, **_kwargs: None)

    def _source(source_id, content, *, budget):
        seen_budgets.append(budget)
        return utils.extract_source_summary(source_id, content, budget=budget)

    def _documents(*_args, budget, **_kwargs):
        seen_budgets.append(budget)
        for chunk in ("chunk one", "chunk two"):
            utils.generate_contextual_embedding("document", chunk, budget=budget)
        return 0

    def _code(args):
        code, before, after, budget = args
        seen_budgets.append(budget)
        return utils.generate_code_example_summary(code, before, after, budget=budget)

    monkeypatch.setattr(mod, "extract_source_summary", _source)
    monkeypatch.setattr(mod, "add_documents_to_db", _documents)
    monkeypatch.setattr(mod, "process_code_example", _code)
    monkeypatch.setenv("USE_AGENTIC_RAG", "true")

    budget = utils.LLMBudget(seconds=60, max_calls=2)
    mod._index_crawl_payload(
        None,
        {"example.test": "source"},
        {"example.test": 1},
        ["https://example.test/page"],
        [0],
        ["chunk one"],
        [{}],
        {"https://example.test/page": "document"},
        [{"url": "https://example.test/page", "markdown": "content"}],
        20,
        budget=budget,
    )

    assert seen_budgets and all(item is budget for item in seen_budgets)
    assert len(calls) == 2
    assert budget.stop_reason == "budget_calls"


def test_deferred_job_registers_then_unregisters_its_budget(monkeypatch):
    import asyncio
    import crawl4ai_mcp as mod

    job_id = "11111111-2222-3333-4444-555555555554"
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    budget = utils.LLMBudget(seconds=60, max_calls=1)
    monkeypatch.setenv("USE_CONTEXTUAL_EMBEDDINGS", "true")
    monkeypatch.setattr(mod, "ensure_index_jobs_table", lambda: None)
    monkeypatch.setattr(mod, "create_index_job", lambda _total: job_id)
    monkeypatch.setattr(mod, "start_index_job", lambda _job_id: None)
    monkeypatch.setattr(mod, "finish_index_job", lambda *_args, **_kwargs: None)

    def _work(_job_id):
        started.set()
        release.wait(1)
        finished.set()

    async def _scenario():
        returned_id = await mod._dispatch_indexing(_work, total=1, budget=budget)
        assert returned_id == job_id
        await asyncio.to_thread(started.wait, 1)
        with mod._INDEX_JOB_BUDGETS_LOCK:
            assert mod._INDEX_JOB_BUDGETS.get(job_id) is budget
        release.set()
        await asyncio.to_thread(finished.wait, 1)
        for _ in range(20):
            await asyncio.sleep(0.01)
            with mod._INDEX_JOB_BUDGETS_LOCK:
                if job_id not in mod._INDEX_JOB_BUDGETS:
                    return
        raise AssertionError("job budget was left registered after its worker finished")

    try:
        asyncio.run(_scenario())
    finally:
        release.set()
        with mod._INDEX_JOB_BUDGETS_LOCK:
            mod._INDEX_JOB_BUDGETS.pop(job_id, None)


def test_deleting_a_queued_job_stops_llm_calls_but_stores_raw_chunks(monkeypatch):
    import asyncio
    import crawl4ai_mcp as mod
    from starlette.testclient import TestClient

    job_id = "11111111-2222-3333-4444-555555555557"
    budget = utils.LLMBudget(seconds=60, max_calls=60)
    calls = []
    stored = []
    finished = threading.Event()
    gate = asyncio.Semaphore(0)
    job = {"id": job_id, "state": "queued"}

    monkeypatch.setenv("USE_CONTEXTUAL_EMBEDDINGS", "true")
    monkeypatch.setattr(mod, "ensure_index_jobs_table", lambda: None)
    monkeypatch.setattr(mod, "create_index_job", lambda _total: job_id)
    monkeypatch.setattr(mod, "get_index_job", lambda _job_id: job)
    monkeypatch.setattr(mod, "start_index_job", lambda _job_id: None)
    monkeypatch.setattr(mod, "finish_index_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "_index_slots", lambda: gate)

    async def _beat(_job_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(mod, "_beat_index_job", _beat)

    def _work(_job_id):
        for chunk in range(3):
            if budget.acquire():
                calls.append(chunk)
            stored.append(chunk)
        finished.set()

    async def _scenario():
        returned_id = await mod._dispatch_indexing(_work, total=3, budget=budget)
        response = TestClient(mod.mcp.streamable_http_app()).delete(f"/jobs/{returned_id}")
        assert response.status_code == 202
        gate.release()
        await asyncio.to_thread(finished.wait, 1)

    try:
        asyncio.run(_scenario())
    finally:
        with mod._INDEX_JOB_BUDGETS_LOCK:
            mod._INDEX_JOB_BUDGETS.pop(job_id, None)

    assert calls == []
    assert stored == [0, 1, 2]


def test_delete_returns_the_budget_stop_reason_already_reached(monkeypatch):
    import crawl4ai_mcp as mod
    from starlette.testclient import TestClient

    job_id = "11111111-2222-3333-4444-555555555561"
    budget = utils.LLMBudget(seconds=60, max_calls=0)
    assert not budget.acquire()
    assert budget.stop_reason == "budget_calls"
    monkeypatch.setattr(mod, "get_index_job", lambda _job_id: {"id": job_id, "state": "queued"})
    with mod._INDEX_JOB_BUDGETS_LOCK:
        mod._INDEX_JOB_BUDGETS[job_id] = budget
    try:
        response = TestClient(mod.mcp.streamable_http_app()).delete(f"/jobs/{job_id}")
    finally:
        with mod._INDEX_JOB_BUDGETS_LOCK:
            mod._INDEX_JOB_BUDGETS.pop(job_id, None)

    assert response.status_code == 202
    assert response.json() == {"id": job_id, "stop_reason": "budget_calls"}



def test_delete_rechecks_terminal_job_after_worker_unregistered_budget(monkeypatch):
    import asyncio
    import crawl4ai_mcp as mod
    from starlette.testclient import TestClient

    job_id = "11111111-2222-3333-4444-555555555558"
    budget = utils.LLMBudget(seconds=60, max_calls=1)
    job = {"id": job_id, "state": "running"}
    release_work = threading.Event()
    worker_unregistered = threading.Event()
    reads = []

    monkeypatch.setattr(mod, "start_index_job", lambda _job_id: None)
    monkeypatch.setattr(
        mod,
        "finish_index_job",
        lambda _job_id, error=None, stop_reason=None: job.update({"state": "done"}),
    )

    async def _beat(_job_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(mod, "_beat_index_job", _beat)

    def _work(_job_id):
        release_work.wait(1)

    def _run_worker():
        asyncio.run(mod._run_index_job(job_id, _work, budget=budget))
        worker_unregistered.set()

    def _get_job(_job_id):
        reads.append(_job_id)
        if len(reads) == 1:
            running_snapshot = job.copy()
            release_work.set()
            assert worker_unregistered.wait(1), "worker did not finish"
            return running_snapshot
        return job

    monkeypatch.setattr(mod, "get_index_job", _get_job)
    with mod._INDEX_JOB_BUDGETS_LOCK:
        mod._INDEX_JOB_BUDGETS[job_id] = budget
    worker = threading.Thread(target=_run_worker)
    worker.start()

    try:
        response = TestClient(mod.mcp.streamable_http_app()).delete(f"/jobs/{job_id}")
    finally:
        release_work.set()
        worker.join(1)
        with mod._INDEX_JOB_BUDGETS_LOCK:
            mod._INDEX_JOB_BUDGETS.pop(job_id, None)

    assert response.status_code == 409
    assert response.json() == {"error": "job already finished"}
    assert reads == [job_id, job_id]


def test_worker_persists_terminal_state_before_unregistering_budget(monkeypatch):
    import asyncio
    import crawl4ai_mcp as mod

    job_id = "11111111-2222-3333-4444-555555555559"
    budget = utils.LLMBudget(seconds=60, max_calls=1)
    registered_when_finished = None

    monkeypatch.setattr(mod, "start_index_job", lambda _job_id: None)

    def _finish(_job_id, error=None, stop_reason=None):
        nonlocal registered_when_finished
        with mod._INDEX_JOB_BUDGETS_LOCK:
            registered_when_finished = mod._INDEX_JOB_BUDGETS.get(job_id)

    monkeypatch.setattr(mod, "finish_index_job", _finish)
    with mod._INDEX_JOB_BUDGETS_LOCK:
        mod._INDEX_JOB_BUDGETS[job_id] = budget

    try:
        asyncio.run(mod._run_index_job(job_id, lambda _job_id: None, budget=budget))
    finally:
        with mod._INDEX_JOB_BUDGETS_LOCK:
            mod._INDEX_JOB_BUDGETS.pop(job_id, None)

    assert registered_when_finished is budget


def test_cancelling_job_does_not_persist_terminal_state(monkeypatch):
    import asyncio
    import crawl4ai_mcp as mod

    job_id = "11111111-2222-3333-4444-555555555560"
    budget = utils.LLMBudget(seconds=60, max_calls=1)
    work_started = threading.Event()
    release_work = threading.Event()
    work_finished = threading.Event()
    finish_calls = []

    monkeypatch.setattr(mod, "start_index_job", lambda _job_id: None)
    monkeypatch.setattr(
        mod,
        "finish_index_job",
        lambda _job_id, error=None, stop_reason=None: finish_calls.append((error, stop_reason)),
    )

    async def _beat(_job_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(mod, "_beat_index_job", _beat)

    def _work(_job_id):
        work_started.set()
        release_work.wait(1)
        work_finished.set()

    async def _scenario():
        task = asyncio.create_task(mod._run_index_job(job_id, _work, budget=budget))
        assert await asyncio.to_thread(work_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not work_finished.is_set()
        with mod._INDEX_JOB_BUDGETS_LOCK:
            assert job_id not in mod._INDEX_JOB_BUDGETS
        assert budget.stop_reason == "cancelled"
        release_work.set()
        assert await asyncio.to_thread(work_finished.wait, 1)

    with mod._INDEX_JOB_BUDGETS_LOCK:
        mod._INDEX_JOB_BUDGETS[job_id] = budget
    try:
        asyncio.run(_scenario())
    finally:
        release_work.set()
        with mod._INDEX_JOB_BUDGETS_LOCK:
            mod._INDEX_JOB_BUDGETS.pop(job_id, None)

    assert finish_calls == []


def test_worker_persists_its_budget_stop_reason(monkeypatch):
    import asyncio
    import crawl4ai_mcp as mod

    captured = {}
    monkeypatch.setattr(mod, "start_index_job", lambda _job_id: None)
    monkeypatch.setattr(
        mod,
        "finish_index_job",
        lambda _job_id, error=None, stop_reason=None: captured.update(
            {"error": error, "stop_reason": stop_reason}
        ),
    )
    budget = utils.LLMBudget(seconds=60, max_calls=1)
    budget.cancel("cancelled")

    asyncio.run(mod._run_index_job("job-1", lambda _job_id: None, budget=budget))

    assert captured == {"error": None, "stop_reason": "cancelled"}


def test_worker_unregisters_its_budget_when_starting_the_job_fails(monkeypatch):
    import asyncio
    import crawl4ai_mcp as mod

    job_id = "11111111-2222-3333-4444-555555555556"
    budget = utils.LLMBudget(seconds=60, max_calls=1)
    captured = {}

    def _raise_start(_job_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(mod, "start_index_job", _raise_start)
    monkeypatch.setattr(
        mod,
        "finish_index_job",
        lambda _job_id, error=None, stop_reason=None: captured.update(
            {"error": error, "stop_reason": stop_reason}
        ),
    )
    with mod._INDEX_JOB_BUDGETS_LOCK:
        mod._INDEX_JOB_BUDGETS[job_id] = budget

    try:
        asyncio.run(mod._run_index_job(job_id, lambda _job_id: None, budget=budget))
    finally:
        with mod._INDEX_JOB_BUDGETS_LOCK:
            registered = mod._INDEX_JOB_BUDGETS.pop(job_id, None)

    assert registered is None
    assert captured == {"error": "RuntimeError: database unavailable", "stop_reason": None}


def test_inline_cancellation_stops_future_llm_calls_but_keeps_indexing_chunks():
    import asyncio
    import crawl4ai_mcp as mod

    budget = utils.LLMBudget(seconds=60, max_calls=50)
    first_call_started = threading.Event()
    release_first_call = threading.Event()
    work_finished = threading.Event()
    llm_calls = []
    stored = []

    def _work(_job_id):
        for chunk in range(50):
            if budget.acquire():
                llm_calls.append(chunk)
                if chunk == 0:
                    first_call_started.set()
                    release_first_call.wait(1)
            stored.append(chunk)
        work_finished.set()

    async def _scenario():
        task = asyncio.create_task(
            mod._dispatch_indexing(_work, total=50, allow_defer=False, budget=budget)
        )
        await asyncio.to_thread(first_call_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release_first_call.set()
        await asyncio.to_thread(work_finished.wait, 2)

    asyncio.run(_scenario())

    assert budget.stop_reason == "client_gone"
    assert llm_calls == [0], f"LLM calls continued after cancellation: {llm_calls}"
    assert stored == list(range(50)), "chunks after cancellation were not indexed"


def test_budgeted_client_does_not_retry_a_timeout(monkeypatch):
    import httpx

    attempts = 0
    real_openai = utils.openai.OpenAI

    def _handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=request)

    def _openai(**kwargs):
        return real_openai(
            **kwargs,
            http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
        )

    monkeypatch.setattr(utils.openai, "OpenAI", _openai)
    monkeypatch.setenv("CONTEXTUAL_LLM_MAX_RETRIES", "0")
    monkeypatch.setenv("MODEL_CHOICE", "test-model")

    contextual, succeeded = utils.generate_contextual_embedding("document", "chunk")

    assert contextual == "chunk"
    assert not succeeded
    assert attempts == 1
