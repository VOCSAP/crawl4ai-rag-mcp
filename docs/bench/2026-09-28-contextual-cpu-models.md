# Contextual embeddings CPU benchmark

## Status

**Reference completed: disable contextual embeddings for this configuration.**

The Qwen3:8B 8000-character condition lost 0.045496 MRR against raw chunks, below the operator threshold of +0.02, and its paired 95% bootstrap interval includes zero. The 4000-character condition also lost MRR. No smaller candidate was run.

**Decision (2026-09-29):** the operator set `USE_CONTEXTUAL_EMBEDDINGS=false` on LXC 122. Phase 5 of `docs/superpowers/specs/2026-09-28-llm-indexing-budget-design.md` (a dedicated CPU model for the contextual step) is dropped. The LLM budget stays in force for source and code summaries.

## Scope and method

- Inference: local loopback-only `llama-server` with Qwen3-8B-Q4_K_M, `-ngl 0`, `--reasoning off`, `-c 5120`, `--parallel 1`, and six CPU threads.
- Embeddings: locally cached `BAAI/bge-m3` on CPU with `HF_HUB_OFFLINE=1`.
- Corpus: 300 chunks from 21 pages. The reviewed 60 questions and their target chunk IDs are unchanged. The expanded corpus adds 209 distractors, including closely related Node.js `path`, `stream`, `buffer`, and `process` pages, MDN HTTP headers, caching, and CORS pages, and Crawl4AI core pages.
- Contextual conditions: every one of the 300 chunks, including every distractor, receives a model-generated context. The embedded retrieval text is `context + "---" + chunk`.
- Retrieval: MRR and recall@5 evaluate each condition against all 300 chunks. The runner persists the rank and source URL for all 60 questions.
- Uncertainty: a deterministic, paired 1,000-sample bootstrap calculates the 95% interval for contextual MRR gain versus raw chunks.

The shared Ollama host was not used.

## Retrieval quality

| Condition | Recall@5 | MRR | MRR gain vs raw | Paired 95% CI | p50 s/chunk | p95 s/chunk | RSS bytes |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| Raw chunks | 0.850000 | 0.658513 | 0 | [0, 0] | -- | -- | -- |
| Qwen3:8B, document 8000 | 0.866667 | 0.613016 | -0.045496 | [-0.127906, +0.036574] | 5.349 | 7.374 | 13,355,110,400 |
| Qwen3:8B, document 4000 | 0.850000 | 0.588444 | -0.070068 | [-0.150909, +0.007530] | 4.907 | 6.805 | 12,826,140,672 |

## MRR by source

| Question source | Raw | 8000 | 4000 |
| --- | ---: | ---: | ---: |
| Node.js fs | 0.537989 | 0.585000 | 0.609286 |
| Python asyncio tasks | 0.660568 | 0.555287 | 0.590428 |
| Crawl4AI quickstart | 0.410714 | 0.393750 | 0.437500 |
| MDN content negotiation | 0.875000 | 0.458333 | 0.708333 |
| FastAPI request body | 0.750000 | 0.666667 | 0.750000 |
| Django queries | 0.595679 | 0.611111 | 0.472222 |
| MDN JavaScript functions | 0.682900 | 0.772894 | 0.654762 |
| CNIL AI | 1.000000 | 0.833333 | 0.833333 |
| Simon Willison 2025 | 1.000000 | 1.000000 | 0.750000 |
| Fowler continuous integration | 0.534416 | 0.460884 | 0.413265 |

## Cost on the first 91-chunk corpus

Qwen3:8B, document 8000, 15-chunk subset of one page, seconds per chunk, p50/p95, prompt cache disabled -> enabled (`--cache-reuse`):

| Threads | Cache off | Cache on |
| ---: | --- | --- |
| 2 | 13.003 / 16.340 | 11.289 / 15.698 |
| 4 | 9.145 / 10.668 | 7.918 / 9.975 |
| 6 | 8.526 / 10.427 | 7.178 / 9.665 |

The prefix cache saves about 13%, not the decisive gain the spec expected. Outputs were identical with and without cache on 5 chunks at temperature 0. These figures come from a Ryzen 9 9900X3D and do not transfer to the Ryzen 5 7640HS production host. On that first corpus (91 chunks), document 8000 gained +0.002 MRR (raw 0.730) and document 4000 lost 0.091: the pool was too easy (raw recall@5 0.95), hence the 300-chunk rerun above.

## Format findings

Both contextual conditions report 27 findings, all `language:en`: the model wrote English context for French source chunks. No finding reported an empty output, preamble, or context length overflow.

## Result artifact

`bench/contextual/data/qwen3-8b-expanded-results.jsonl` is local and ignored by Git. It contains the three conditions, 60 per-question ranks for each condition, source URLs, source-level MRR, and the paired bootstrap intervals.
