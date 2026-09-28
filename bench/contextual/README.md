# Contextual-embedding CPU benchmark

This directory measures retrieval quality and local CPU cost for contextual embeddings. It never contacts the shared Ollama service: `run_benchmark.py` starts a temporary loopback-only `llama-server` with `-ngl 0`.

## 1. Collect the corpus

```bash
.venv/Scripts/python.exe bench/contextual/collect_corpus.py \
  --max-chunks 300 \
  --questions bench/contextual/data/questions.jsonl
```

The collector fetches the configured benchmark pages, writes raw Markdown under `data/documents/`, then uses the project's `smart_chunk_markdown`. It reserves the first 15 chunks from the first page for cache and cost measurements, then fills the 300-chunk quality corpus round-robin across sources. The expanded pool retains the reviewed 60-question targets and adds nearby Node.js, MDN HTTP, and Crawl4AI documentation as hard negatives. All generated data is ignored by Git.

## 2. Generate reviewed questions

Review the raw corpus before generating questions. Set `question_eligible` to `false` for navigation and filler chunks: they remain indexed as retrieval distractors but receive no question. Add enough raw content chunks to retain 60 eligible targets.

```bash
.venv/Scripts/python.exe bench/contextual/generate_questions.py
```

Generate one realistic question for each eligible raw chunk only. Use the chunk alone, preserve its language, and paraphrase rather than repeating rare chunk terms. Save JSONL objects as `data/questions.jsonl`, then validate them:

```bash
.venv/Scripts/python.exe bench/contextual/generate_questions.py --questions bench/contextual/data/questions.jsonl
```

Review each question for answerability from its sole chunk and record lexical overlap before launching a model.

## 3. Run a smoke test

```bash
.venv/Scripts/python.exe bench/contextual/run_benchmark.py \
  --questions bench/contextual/data/questions.jsonl \
  --model <local-q4-gguf> \
  --llama-server <local-llama-server> \
  --embedding-model <local-sentence-transformers-model> \
  --threads 2 --parallel 1 --ctx-size 5120 --cache-modes enabled --doc-truncations 8000 \
  --question-limit 5 --skip-cost --skip-coherence \
  --output bench/contextual/data/smoke-results.jsonl
```

The runner records a raw-chunk baseline and the contextual condition. It starts each contextual condition with explicit `--threads`, `--parallel`, `--cache-reuse` or disabled prompt cache, `--reasoning off`, and CPU-only inference. It starts `llama-server` with `-c 5120` to bound the context allocation. Disabling reasoning is required because this llama.cpp build otherwise emits no final `message.content` within the production 200-token response limit.

## 4. Run one model's full matrix

```bash
.venv/Scripts/python.exe bench/contextual/run_benchmark.py \
  --questions bench/contextual/data/questions.jsonl \
  --model <local-gguf> \
  --llama-server <local-llama-server> \
  --embedding-model <local-sentence-transformers-model> \
  --threads 2 4 6 --parallel 1 --ctx-size 5120 \
  --cache-modes disabled enabled --cache-reuse 256 \
  --doc-truncations 8000 4000 --cost-doc-truncation 8000 \
  --quality-threads 2 --quality-cache-mode enabled \
  --cost-chunks 15 --coherence-chunks 5 \
  --output bench/contextual/data/results.jsonl
```

Quality runs once per document truncation on all eligible questions. Cost runs the same-page 15-chunk subset for every thread and cache combination. The coherence check compares five temperature-zero outputs with cache disabled and enabled. Each completed condition is written immediately to JSONL and is skipped on a resumed run. Quality results contain per-question ranks and source URLs, recall@5, MRR, a deterministic 1,000-sample paired-bootstrap 95% interval for the MRR gain over raw chunks, median and p95 seconds per chunk, format findings, and the server working-set RSS. Repeat the full matrix once for the reference model and once per candidate. Compare relative MRR gain with the decision rule in the design specification, not the smoke-test score.
