import argparse
import asyncio
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from common import read_jsonl, smart_chunk_markdown, write_jsonl

DEFAULT_SOURCES = [
    ("https://nodejs.org/api/fs.html", "en"),
    ("https://docs.python.org/3/library/asyncio-task.html", "en"),
    ("https://docs.crawl4ai.com/core/quickstart/", "en"),
    ("https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Content_negotiation", "en"),
    ("https://fastapi.tiangolo.com/tutorial/body/", "en"),
    ("https://docs.djangoproject.com/en/5.2/topics/db/queries/", "en"),
    ("https://developer.mozilla.org/fr/docs/Web/JavaScript/Guide/Functions", "fr"),
    ("https://www.cnil.fr/fr/intelligence-artificielle", "fr"),
    ("https://simonwillison.net/2025/", "en"),
    ("https://martinfowler.com/articles/continuousIntegration.html", "en"),
    ("https://nodejs.org/api/path.html", "en"),
    ("https://nodejs.org/api/stream.html", "en"),
    ("https://nodejs.org/api/buffer.html", "en"),
    ("https://nodejs.org/api/process.html", "en"),
    ("https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers", "en"),
    ("https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Caching", "en"),
    ("https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS", "en"),
    ("https://docs.crawl4ai.com/core/async-webcrawler/", "en"),
    ("https://docs.crawl4ai.com/core/crawler-config/", "en"),
    ("https://docs.crawl4ai.com/core/fit-markdown/", "en"),
    ("https://docs.crawl4ai.com/core/markdown-generation/", "en"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect local raw-markdown corpus for contextual benchmark.")
    parser.add_argument("--output", type=Path, default=Path("bench/contextual/data/chunks.jsonl"))
    parser.add_argument("--chunk-size", type=int, default=5000)
    parser.add_argument("--max-chunks", type=int, default=60)
    parser.add_argument("--cost-chunks", type=int, default=15)
    parser.add_argument("--documents-dir", type=Path, default=Path("bench/contextual/data/documents"))
    parser.add_argument("--urls-file", type=Path)
    parser.add_argument("--questions", type=Path)
    return parser.parse_args()


def sources_from_file(path: Path) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        url, language = line.split(maxsplit=1)
        sources.append((url, language))
    return sources


async def collect(args: argparse.Namespace) -> list[dict[str, object]]:
    sources = sources_from_file(args.urls_file) if args.urls_file else DEFAULT_SOURCES
    chunks_by_source: list[list[dict[str, object]]] = []
    args.documents_dir.mkdir(parents=True, exist_ok=True)
    browser_config = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for source_index, (url, language) in enumerate(sources):
            result = await crawler.arun(url=url, config=run_config)
            if not result.success or not result.markdown:
                print(f"skip {url}: {result.error_message}")
                chunks_by_source.append([])
                continue
            document = str(result.markdown)
            document_path = args.documents_dir / f"source-{source_index}.md"
            document_path.write_text(document, encoding="utf-8", newline="\n")
            chunks_by_source.append(
                [
                    {
                        "chunk_id": f"source-{source_index}-chunk-{chunk_index}",
                        "url": url,
                        "language": language,
                        "document_path": str(document_path.relative_to(args.output.parent)),
                        "chunk": chunk,
                        "cost_subset": source_index == 0 and chunk_index < args.cost_chunks,
                    }
                    for chunk_index, chunk in enumerate(smart_chunk_markdown(document, chunk_size=args.chunk_size))
                ]
            )
    records = chunks_by_source[0][: min(args.cost_chunks, args.max_chunks)] if chunks_by_source else []
    selected_ids = {str(record["chunk_id"]) for record in records}
    required_ids = [str(question["chunk_id"]) for question in read_jsonl(args.questions)] if args.questions else []
    available = {
        str(record["chunk_id"]): record
        for source_chunks in chunks_by_source
        for record in source_chunks
    }
    missing_required_ids = [chunk_id for chunk_id in required_ids if chunk_id not in available]
    if missing_required_ids:
        raise SystemExit(f"Question chunks are missing from the collected corpus: {', '.join(missing_required_ids)}")
    for chunk_id in required_ids:
        if chunk_id not in selected_ids:
            records.append(available[chunk_id])
            selected_ids.add(chunk_id)
    if len(records) > args.max_chunks:
        raise SystemExit("max-chunks must accommodate the cost subset and question chunks.")
    for chunk_index in range(max(map(len, chunks_by_source), default=0)):
        for source_chunks in chunks_by_source:
            if chunk_index >= len(source_chunks):
                continue
            record = source_chunks[chunk_index]
            if str(record["chunk_id"]) in selected_ids:
                continue
            records.append(record)
            selected_ids.add(str(record["chunk_id"]))
            if len(records) >= args.max_chunks:
                return records
    return records


def main() -> None:
    args = parse_args()
    records = asyncio.run(collect(args))
    if not records:
        raise SystemExit("No chunks collected.")
    write_jsonl(args.output, records)
    print(f"wrote {len(records)} chunks to {args.output}")


if __name__ == "__main__":
    main()
