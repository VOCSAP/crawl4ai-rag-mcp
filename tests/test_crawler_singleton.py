"""Regression tests for the process-wide Chromium crawler singleton.

Runnable without pytest (``python tests/test_crawler_singleton.py``) so it can be
executed inside the mcp-crawl4ai container, which ships no test dependencies.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("USE_RERANKING", "false")
os.environ.setdefault("USE_KNOWLEDGE_GRAPH", "false")
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:5432/unused")

import crawl4ai_mcp as mod

LIFESPAN_REENTRIES = 5


class FakeCrawler:
    """Stands in for AsyncWebCrawler, counting instantiations and teardowns."""

    instances = 0
    closed = 0

    def __init__(self, config=None):
        type(self).instances += 1
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        type(self).closed += 1
        return False


def _reset(monkeypatch):
    FakeCrawler.instances = 0
    FakeCrawler.closed = 0
    monkeypatch.setattr(mod, "AsyncWebCrawler", FakeCrawler)
    # raising=False keeps the test meaningful against the pre-fix code, where the
    # module-level singleton does not exist yet: it then fails on the instance
    # count, which is the defect under test, rather than on an AttributeError.
    monkeypatch.setattr(mod, "_shared_crawler", None, raising=False)


def test_one_crawler_across_lifespan_reentries(monkeypatch):
    _reset(monkeypatch)

    async def _run():
        seen = []
        for _ in range(LIFESPAN_REENTRIES):
            async with mod.crawl4ai_lifespan(mod.mcp) as ctx:
                seen.append(await ctx.get_crawler())
        return seen

    seen = asyncio.run(_run())

    assert FakeCrawler.instances == 1, (
        f"{LIFESPAN_REENTRIES} lifespan re-entries started {FakeCrawler.instances} "
        "Chromium crawlers; each leaks a process tree when the lifespan finally is "
        "bypassed by anyio TaskGroup cancellation"
    )
    assert all(c is seen[0] for c in seen)


def test_lifespan_exit_does_not_close_the_shared_crawler(monkeypatch):
    _reset(monkeypatch)

    async def _run():
        async with mod.crawl4ai_lifespan(mod.mcp) as ctx:
            await ctx.get_crawler()
        # A second session must still get a live crawler, not a closed one.
        async with mod.crawl4ai_lifespan(mod.mcp) as ctx:
            return await ctx.get_crawler()

    crawler = asyncio.run(_run())

    assert FakeCrawler.closed == 0, (
        "one MCP session closed the Chromium shared with every other session"
    )
    assert crawler is not None


def test_concurrent_first_calls_start_a_single_crawler(monkeypatch):
    _reset(monkeypatch)

    async def _run():
        async with mod.crawl4ai_lifespan(mod.mcp) as ctx:
            return await asyncio.gather(*(ctx.get_crawler() for _ in range(10)))

    seen = asyncio.run(_run())

    assert FakeCrawler.instances == 1
    assert all(c is seen[0] for c in seen)


if __name__ == "__main__":
    class _MiniMonkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value, raising=True):
            if raising and not hasattr(target, name):
                raise AttributeError(f"{target!r} has no attribute {name!r}")
            self._undo.append((target, name, getattr(target, name, None), hasattr(target, name)))
            setattr(target, name, value)

        def undo(self):
            for target, name, old, existed in reversed(self._undo):
                if existed:
                    setattr(target, name, old)
                else:
                    delattr(target, name)
            self._undo.clear()

    failures = 0
    for fn in (
        test_one_crawler_across_lifespan_reentries,
        test_lifespan_exit_does_not_close_the_shared_crawler,
        test_concurrent_first_calls_start_a_single_crawler,
    ):
        mp = _MiniMonkeypatch()
        try:
            fn(mp)
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
        finally:
            mp.undo()
    sys.exit(1 if failures else 0)
