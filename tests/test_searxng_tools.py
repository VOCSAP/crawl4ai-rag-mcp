"""Tests for the SearXNG tools: the engines filter and the unresponsive engines report.

Runnable without pytest (``python tests/test_searxng_tools.py``) so it can be
executed inside the mcp-crawl4ai container, which ships no test dependencies.
SearXNG itself is stubbed: what is under test is the request we send and the
answer we build from its JSON.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("USE_RERANKING", "false")
os.environ.setdefault("USE_KNOWLEDGE_GRAPH", "false")
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:5432/unused")
os.environ["SEARXNG_URL"] = "http://searxng.test"

import crawl4ai_mcp as mod

HIT = {"title": "Wikipedia", "url": "https://www.wikipedia.org/", "content": "x", "engine": "brave"}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _stub_searxng(monkeypatch, payload):
    """Replace requests.get and return the list of params each call sent."""
    sent = []

    def _get(url, params=None, headers=None, timeout=None):
        sent.append(dict(params or {}))
        return FakeResponse(payload)

    monkeypatch.setattr(mod.requests, "get", _get)
    return sent


def _call(tool, **kwargs):
    return json.loads(asyncio.run(tool(None, **kwargs)))


def test_an_engines_filter_is_not_widened_by_a_category(monkeypatch):
    for tool in (mod.searxng_search, mod.searxng_images, mod.searxng_news):
        sent = _stub_searxng(monkeypatch, {"results": [HIT]})
        _call(tool, query="wikipedia", engines="duckduckgo")
        assert sent[0].get("engines") == "duckduckgo"
        assert "categories" not in sent[0], (
            f"{tool.__name__} sent categories={sent[0].get('categories')!r} next to engines: "
            "SearXNG takes the union, so the engines filter selects nothing"
        )


def test_without_engines_the_category_is_still_sent(monkeypatch):
    for tool, category in ((mod.searxng_search, "general"),
                           (mod.searxng_images, "images"),
                           (mod.searxng_news, "news")):
        sent = _stub_searxng(monkeypatch, {"results": [HIT]})
        _call(tool, query="wikipedia")
        assert sent[0].get("categories") == category, f"{tool.__name__} sent {sent[0]}"


def test_unresponsive_engines_are_reported_with_their_reason(monkeypatch):
    payload = {"results": [HIT], "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["brave", "too many requests"]]}
    for tool in (mod.searxng_search, mod.searxng_images, mod.searxng_news):
        _stub_searxng(monkeypatch, payload)
        body = _call(tool, query="wikipedia")
        assert body["unresponsive_engines"] == [
            {"engine": "duckduckgo", "reason": "CAPTCHA"},
            {"engine": "brave", "reason": "too many requests"},
        ], f"{tool.__name__} answered {body}"
        assert "warning" not in body, "results came back, the outage of some engines is not a warning"


def test_an_empty_answer_from_failing_engines_carries_a_warning(monkeypatch):
    _stub_searxng(monkeypatch, {"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]})
    body = _call(mod.searxng_search, query="wikipedia")
    assert body["success"] is True
    assert body["count"] == 0
    assert "duckduckgo (CAPTCHA)" in body.get("warning", ""), f"no usable warning in {body}"


def test_an_empty_answer_with_healthy_engines_is_not_a_warning(monkeypatch):
    _stub_searxng(monkeypatch, {"results": []})
    body = _call(mod.searxng_search, query="an unlikely query")
    assert body["unresponsive_engines"] == []
    assert "warning" not in body


def test_search_names_the_failing_engines_when_nothing_came_back(monkeypatch):
    sent = _stub_searxng(monkeypatch, {"results": [], "unresponsive_engines": [["google", "timeout"]]})
    os.environ["SEARXNG_DEFAULT_ENGINES"] = "google"
    try:
        body = _call(mod.search, query="wikipedia")
    finally:
        del os.environ["SEARXNG_DEFAULT_ENGINES"]
    assert body["success"] is False
    assert "categories" not in sent[0], f"search sent {sent[0]} next to SEARXNG_DEFAULT_ENGINES"
    assert body["unresponsive_engines"] == [{"engine": "google", "reason": "timeout"}], body


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
        test_an_engines_filter_is_not_widened_by_a_category,
        test_without_engines_the_category_is_still_sent,
        test_unresponsive_engines_are_reported_with_their_reason,
        test_an_empty_answer_from_failing_engines_carries_a_warning,
        test_an_empty_answer_with_healthy_engines_is_not_a_warning,
        test_search_names_the_failing_engines_when_nothing_came_back,
    ):
        mp = _MiniMonkeypatch()
        try:
            fn(mp)
            print(f"PASS {fn.__name__}")
        except (AssertionError, KeyError) as exc:
            failures += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
        finally:
            mp.undo()
    sys.exit(1 if failures else 0)
