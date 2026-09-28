import argparse
import ctypes
import json
import socket
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from common import contextual_messages, format_issues, read_jsonl, write_jsonl


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure contextual-retrieval quality and local llama.cpp CPU cost.")
    parser.add_argument("--chunks", type=Path, default=Path("bench/contextual/data/chunks.jsonl"))
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--llama-server", type=Path, required=True)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--output", type=Path, default=Path("bench/contextual/data/results.jsonl"))
    parser.add_argument("--threads", type=int, nargs="+", default=[2, 4, 6])
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--ctx-size", type=int, default=5120)
    parser.add_argument("--cache-modes", choices=["disabled", "enabled"], nargs="+", default=["disabled", "enabled"])
    parser.add_argument("--cache-reuse", type=int, default=256)
    parser.add_argument("--doc-truncations", type=int, nargs="+", default=[8000, 4000])
    parser.add_argument("--cost-doc-truncation", type=int, default=8000)
    parser.add_argument("--quality-threads", type=int, default=2)
    parser.add_argument("--quality-cache-mode", choices=["disabled", "enabled"], default="enabled")
    parser.add_argument("--cost-chunks", type=int, default=15)
    parser.add_argument("--coherence-chunks", type=int, default=5)
    parser.add_argument("--max-context-chars", type=int, default=600)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--question-limit", type=int)
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--skip-cost", action="store_true")
    parser.add_argument("--skip-coherence", action="store_true")
    return parser.parse_args()


def select_records(
    chunks_path: Path,
    questions_path: Path,
    limit: int | None,
    question_limit: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chunks = read_jsonl(chunks_path)
    if limit:
        chunks = chunks[:limit]
    chunk_ids = {str(record["chunk_id"]) for record in chunks}
    questions = [record for record in read_jsonl(questions_path) if str(record.get("chunk_id")) in chunk_ids]
    if question_limit:
        questions = questions[:question_limit]
    question_ids = [str(record.get("chunk_id")) for record in questions]
    if len(question_ids) != len(set(question_ids)):
        raise SystemExit("Questions must not contain duplicate chunk ids.")
    if any(not str(record.get("question", "")).strip() for record in questions):
        raise SystemExit("Questions must not be empty.")
    if not chunks:
        raise SystemExit("No chunks selected.")
    if not questions:
        raise SystemExit("No questions selected.")
    return chunks, questions


def cost_records(chunks: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    records = [record for record in chunks if record.get("cost_subset")]
    if len(records) < count:
        raise SystemExit(f"Corpus must contain at least {count} cost_subset chunks from one source.")
    return records[:count]


def document_for(record: dict[str, Any], chunks_path: Path) -> str:
    document_path = chunks_path.parent / str(record["document_path"])
    return document_path.read_text(encoding="utf-8")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def request_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"{url} returned {exc.code}: {body}") from exc


def wait_for_server(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"llama-server exited with status {process.returncode}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except urllib.error.URLError:
            time.sleep(0.5)
    raise TimeoutError("llama-server did not become healthy within 120 seconds")


def stop_server(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def rss_bytes(process: subprocess.Popen[bytes]) -> int | None:
    if process.poll() is not None:
        return None
    handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, process.pid)
    if not handle:
        return None
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def launch_server(args: argparse.Namespace, threads: int, cache_mode: str) -> tuple[subprocess.Popen[bytes], str]:
    port = free_port()
    command = [
        str(args.llama_server),
        "-m",
        str(args.model),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--threads",
        str(threads),
        "--parallel",
        str(args.parallel),
        "-c",
        str(args.ctx_size),
        "--reasoning",
        "off",
        "-ngl",
        "0",
    ]
    if cache_mode == "enabled":
        command.extend(["--cache-prompt", "--cache-reuse", str(args.cache_reuse)])
    else:
        command.append("--no-cache-prompt")
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_for_server(base_url, process)
    except Exception:
        stop_server(process)
        raise
    return process, base_url


def generate_contexts(
    base_url: str,
    chunks: list[dict[str, Any]],
    chunks_path: Path,
    doc_truncation: int,
    max_context_chars: int,
    temperature: float,
) -> tuple[list[str], list[float], list[list[str]]]:
    contexts: list[str] = []
    latencies: list[float] = []
    issues: list[list[str]] = []
    for record in chunks:
        started = time.perf_counter()
        response = request_json(
            f"{base_url}/v1/chat/completions",
            {
                "messages": contextual_messages(document_for(record, chunks_path), str(record["chunk"]), doc_truncation),
                "temperature": temperature,
                "max_tokens": 200,
                "stream": False,
                "think": False,
            },
            timeout=300,
        )
        latencies.append(time.perf_counter() - started)
        context = str(response["choices"][0]["message"]["content"]).strip()
        contexts.append(context)
        issues.append(format_issues(context, str(record["language"]), max_context_chars))
    return contexts, latencies, issues


def percentile95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return float(np.percentile(np.array(values), 95))


def retrieval_metrics(
    encoder: SentenceTransformer,
    chunks: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    documents: list[str],
) -> tuple[float, float, dict[str, float], list[dict[str, Any]]]:
    document_embeddings = encoder.encode(documents, normalize_embeddings=True, show_progress_bar=False)
    question_embeddings = encoder.encode([str(record["question"]) for record in questions], normalize_embeddings=True, show_progress_bar=False)
    positions = {str(record["chunk_id"]): index for index, record in enumerate(chunks)}
    question_ranks: list[dict[str, Any]] = []
    recall_hits = 0
    for question, embedding in zip(questions, question_embeddings, strict=True):
        target = positions[str(question["chunk_id"])]
        ranking = np.argsort(-(document_embeddings @ embedding))
        rank = int(np.where(ranking == target)[0][0]) + 1
        recall_hits += rank <= 5
        question_ranks.append(
            {
                "chunk_id": str(question["chunk_id"]),
                "source_url": str(chunks[target]["url"]),
                "rank": rank,
                "reciprocal_rank": 1 / rank,
            }
        )
    reciprocal_ranks = [float(record["reciprocal_rank"]) for record in question_ranks]
    mrr_by_source = {
        source_url: statistics.fmean(
            float(record["reciprocal_rank"])
            for record in question_ranks
            if str(record["source_url"]) == source_url
        )
        for source_url in sorted({str(record["source_url"]) for record in question_ranks})
    }
    return recall_hits / len(questions), statistics.fmean(reciprocal_ranks), mrr_by_source, question_ranks


def paired_bootstrap_ci(baseline: list[float], contextual: list[float]) -> tuple[float, float]:
    baseline_values = np.array(baseline)
    contextual_values = np.array(contextual)
    generator = np.random.default_rng(20260928)
    samples = generator.integers(0, len(baseline_values), size=(1000, len(baseline_values)))
    gains = contextual_values[samples].mean(axis=1) - baseline_values[samples].mean(axis=1)
    low, high = np.percentile(gains, [2.5, 97.5])
    return float(low), float(high)


def result_record(
    condition: dict[str, Any],
    args: argparse.Namespace,
    recall_at_5: float | None = None,
    mrr: float | None = None,
    mrr_gain_vs_raw: float | None = None,
    mrr_gain_ci_95: tuple[float, float] | None = None,
    mrr_by_source: dict[str, float] | None = None,
    question_ranks: list[dict[str, Any]] | None = None,
    latencies: list[float] | None = None,
    issues: list[list[str]] | None = None,
    rss: int | None = None,
    llama_server_pid: int | None = None,
) -> dict[str, Any]:
    return {
        **condition,
        "model_path": str(args.model),
        "ctx_size": args.ctx_size,
        "recall_at_5": recall_at_5,
        "mrr": mrr,
        "mrr_gain_vs_raw": mrr_gain_vs_raw,
        "mrr_gain_ci_95": mrr_gain_ci_95,
        "mrr_gain_bootstrap_samples": 1000 if mrr_gain_ci_95 is not None else None,
        "mrr_by_source": mrr_by_source,
        "question_ranks": question_ranks,
        "latency_seconds_median": statistics.median(latencies) if latencies else None,
        "latency_seconds_p95": percentile95(latencies) if latencies else None,
        "format_issue_count": sum(bool(item) for item in issues) if issues else 0,
        "format_issues": issues,
        "llama_server_pid": llama_server_pid,
        "llama_server_rss_bytes": rss,
    }


def condition_id(kind: str, args: argparse.Namespace, **values: object) -> str:
    parts = [kind, str(args.model), str(args.ctx_size)]
    parts.extend(f"{key}={value}" for key, value in sorted(values.items()))
    return "|".join(parts)


def append_result(results: list[dict[str, Any]], output: Path, result: dict[str, Any]) -> None:
    results.append(result)
    write_jsonl(output, results)
    print(json.dumps(result, ensure_ascii=False))


def run_coherence_check(args: argparse.Namespace, records: list[dict[str, Any]], results: list[dict[str, Any]], completed: set[str]) -> None:
    identifier = condition_id("coherence", args, chunks=len(records), doc_truncation=args.cost_doc_truncation)
    if identifier in completed:
        return
    outputs: dict[str, list[str]] = {}
    rss: dict[str, int | None] = {}
    for cache_mode in ("disabled", "enabled"):
        process, base_url = launch_server(args, args.quality_threads, cache_mode)
        try:
            contexts, _, _ = generate_contexts(
                base_url,
                records,
                args.chunks,
                args.cost_doc_truncation,
                args.max_context_chars,
                0,
            )
            outputs[cache_mode] = contexts
            rss[cache_mode] = rss_bytes(process)
        finally:
            stop_server(process)
    differing_chunk_ids = [
        str(record["chunk_id"])
        for record, uncached, cached in zip(records, outputs["disabled"], outputs["enabled"], strict=True)
        if uncached != cached
    ]
    append_result(
        results,
        args.output,
        result_record(
            {
                "condition_id": identifier,
                "condition": "coherence",
                "chunks": len(records),
                "temperature": 0,
                "cache_outputs_equal": not differing_chunk_ids,
                "differing_chunk_ids": differing_chunk_ids,
                "cache_disabled_rss_bytes": rss["disabled"],
                "cache_enabled_rss_bytes": rss["enabled"],
            },
            args,
        ),
    )
    completed.add(identifier)


def run_quality(
    args: argparse.Namespace,
    encoder: SentenceTransformer,
    chunks: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    baseline_question_ranks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    completed: set[str],
) -> None:
    for doc_truncation in args.doc_truncations:
        identifier = condition_id(
            "quality",
            args,
            chunks=len(chunks),
            questions=len(questions),
            doc_truncation=doc_truncation,
        )
        if identifier in completed:
            continue
        process, base_url = launch_server(args, args.quality_threads, args.quality_cache_mode)
        try:
            contexts, latencies, issues = generate_contexts(
                base_url,
                chunks,
                args.chunks,
                doc_truncation,
                args.max_context_chars,
                args.temperature,
            )
            recall, mrr, mrr_by_source, question_ranks = retrieval_metrics(
                encoder,
                chunks,
                questions,
                [f"{context}\n---\n{record['chunk']}" for context, record in zip(contexts, chunks, strict=True)],
            )
            baseline_reciprocal_ranks = [float(record["reciprocal_rank"]) for record in baseline_question_ranks]
            reciprocal_ranks = [float(record["reciprocal_rank"]) for record in question_ranks]
            mrr_gain_ci_95 = paired_bootstrap_ci(baseline_reciprocal_ranks, reciprocal_ranks)
            append_result(
                results,
                args.output,
                result_record(
                    {
                        "condition_id": identifier,
                        "condition": "quality",
                        "chunks": len(chunks),
                        "questions": len(questions),
                        "doc_truncation": doc_truncation,
                        "threads": args.quality_threads,
                        "parallel": args.parallel,
                        "cache_mode": args.quality_cache_mode,
                        "cache_reuse": args.cache_reuse if args.quality_cache_mode == "enabled" else 0,
                        "temperature": args.temperature,
                    },
                    args,
                    recall_at_5=recall,
                    mrr=mrr,
                    mrr_gain_vs_raw=mrr - statistics.fmean(baseline_reciprocal_ranks),
                    mrr_gain_ci_95=mrr_gain_ci_95,
                    mrr_by_source=mrr_by_source,
                    question_ranks=question_ranks,
                    latencies=latencies,
                    issues=issues,
                    rss=rss_bytes(process),
                    llama_server_pid=process.pid,
                ),
            )
            completed.add(identifier)
        finally:
            stop_server(process)


def run_cost(args: argparse.Namespace, chunks: list[dict[str, Any]], results: list[dict[str, Any]], completed: set[str]) -> None:
    for threads in args.threads:
        for cache_mode in args.cache_modes:
            identifier = condition_id(
                "cost",
                args,
                chunks=len(chunks),
                threads=threads,
                cache_mode=cache_mode,
                doc_truncation=args.cost_doc_truncation,
            )
            if identifier in completed:
                continue
            process, base_url = launch_server(args, threads, cache_mode)
            try:
                _, latencies, issues = generate_contexts(
                    base_url,
                    chunks,
                    args.chunks,
                    args.cost_doc_truncation,
                    args.max_context_chars,
                    args.temperature,
                )
                append_result(
                    results,
                    args.output,
                    result_record(
                        {
                            "condition_id": identifier,
                            "condition": "cost",
                            "chunks": len(chunks),
                            "doc_truncation": args.cost_doc_truncation,
                            "threads": threads,
                            "parallel": args.parallel,
                            "cache_mode": cache_mode,
                            "cache_reuse": args.cache_reuse if cache_mode == "enabled" else 0,
                            "temperature": args.temperature,
                        },
                        args,
                        latencies=latencies,
                        issues=issues,
                        rss=rss_bytes(process),
                        llama_server_pid=process.pid,
                    ),
                )
                completed.add(identifier)
            finally:
                stop_server(process)


def main() -> None:
    args = parse_args()
    chunks, questions = select_records(args.chunks, args.questions, args.limit, args.question_limit)
    existing = read_jsonl(args.output) if args.output.exists() else []
    completed = {str(record.get("condition_id")) for record in existing}
    encoder = SentenceTransformer(args.embedding_model, device="cpu")
    baseline_recall, baseline_mrr, baseline_mrr_by_source, baseline_question_ranks = retrieval_metrics(
        encoder,
        chunks,
        questions,
        [str(record["chunk"]) for record in chunks],
    )
    baseline_id = condition_id("raw", args, chunks=len(chunks), questions=len(questions))
    if baseline_id not in completed:
        append_result(
            existing,
            args.output,
            result_record(
                {"condition_id": baseline_id, "condition": "raw", "chunks": len(chunks), "questions": len(questions)},
                args,
                recall_at_5=baseline_recall,
                mrr=baseline_mrr,
                mrr_gain_vs_raw=0,
                mrr_gain_ci_95=(0, 0),
                mrr_by_source=baseline_mrr_by_source,
                question_ranks=baseline_question_ranks,
            ),
        )
        completed.add(baseline_id)
    if not args.skip_coherence:
        run_coherence_check(args, cost_records(chunks, args.coherence_chunks), existing, completed)
    if not args.skip_quality:
        run_quality(args, encoder, chunks, questions, baseline_question_ranks, existing, completed)
    if not args.skip_cost:
        run_cost(args, cost_records(chunks, args.cost_chunks), existing, completed)


if __name__ == "__main__":
    main()
