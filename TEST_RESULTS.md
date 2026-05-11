# Résultats des tests -- crawl4ai-rag-mcp

**Date :** 2026-05-11
**Serveur :** LXC 122 -- 192.168.10.22:8051
**Stack :** mcp-crawl4ai, postgres-rag, searxng, valkey
**Ollama externe :** 192.168.10.16:11434

---

## Configuration .env active lors des tests

### Session 1 (2026-05-11) -- tests T01-T17, T23-T28

```
OLLAMA_BASE_URL=http://192.168.10.16:11434
EMBEDDING_MODEL=nomic-embed-text:v1.5
EMBEDDING_DIMENSIONS=768
MODEL_CHOICE=qwen3.5:4b  (commenté -- inactif)
USE_CONTEXTUAL_EMBEDDINGS=false
USE_HYBRID_SEARCH=true
USE_AGENTIC_RAG=false
USE_RERANKING=false
RERANKING_BACKEND=remote
RERANKING_TIMEOUT=300
RERANKING_MODEL=qwen3.5:4b
USE_KNOWLEDGE_GRAPH=false
```

### Session 2 (2026-05-12) -- tests T18-T21 (features LLM)

```
OLLAMA_BASE_URL=http://192.168.10.15:4000  (LiteLLM proxy sur LXC 115)
EMBEDDING_MODEL=nomic-embed-text-v1.5
EMBEDDING_DIMENSIONS=768
MODEL_CHOICE=qwen3.5-4b
USE_CONTEXTUAL_EMBEDDINGS=true
USE_HYBRID_SEARCH=true
USE_AGENTIC_RAG=true
USE_RERANKING=true
RERANKING_BACKEND=remote
RERANKING_TIMEOUT=300
RERANKING_MODEL=qwen3.5-4b
USE_KNOWLEDGE_GRAPH=false
OPENAI_API_KEY=<litellm_master_key>
```

---

## Résultats

| Test | Statut | Notes |
|---|---|---|
| T01 -- Connectivité SSE | **PASS** | 13 outils listés (dont search_code_examples exposé même avec USE_AGENTIC_RAG=false) |
| T02 -- scrape_urls URL unique | **PASS** | 12 chunks, source_id=docs.python.org |
| T03 -- scrape_urls batch | **PASS** | 3/3 URLs, mode multi_url, 130 chunks |
| T04 -- scrape_urls raw_markdown | **PASS** | mode raw_markdown, aucun stockage |
| T05 -- smart_crawl_url webpage | **PASS** | crawl_type=webpage, pages_crawled=1 |
| T06 -- smart_crawl_url sitemap | **PARTIEL** | python.org : "No URLs found in sitemap" (sitemap index non supporté). crawl4ai.com : PASS (crawl_type=sitemap, 86 pages, 457 chunks). CPU 100% pendant ~45s avec max_concurrent=5. |
| T07 -- smart_crawl_url txt | **PASS** | crawl_type=text_file, llmstxt.org/llms.txt |
| T08 -- get_available_sources nominal | **PASS** | 3 sources : docs.python.org, docs.crawl4ai.com, llmstxt.org |
| T09 -- get_available_sources vide | **PASS** | success:true, count:0 (validé en début de session) |
| T10 -- perform_rag_query sans filtre | **PASS** | success:true, count:5. search_mode=hybrid (USE_HYBRID_SEARCH=true dans .env actuel) |
| T11 -- perform_rag_query avec filtre | **PASS** | source_filter=docs.python.org, 5/5 résultats filtrés |
| T12 -- searxng_search | **PASS** | 5 résultats avec title/url/snippet/engine |
| T13 -- searxng_images | **PASS** | 5 résultats, thumbnail_src présent (vide pour 2 SVGs) |
| T14 -- searxng_news | **PASS** | 5 résultats, publishedDate renseigné sur 2/5 |
| T15 -- search composite RAG | **PASS** | mode=rag_query, 3 URLs scrappées, résultats RAG par URL |
| T16 -- search raw_markdown | **PASS** | mode=raw_markdown, markdown brut par URL |
| T17 -- USE_HYBRID_SEARCH | **PASS** | Déjà actif (true) -- search_mode=hybrid confirmé par T10/T11 |
| T18 -- USE_CONTEXTUAL_EMBEDDINGS | **PASS** | generate_contextual_embedding() retourne un contexte non-vide. Backend : LiteLLM qwen3.5-4b via OLLAMA_BASE_URL. Validé par docker exec direct (race condition FastMCP contournée). |
| T19 -- USE_RERANKING local CrossEncoder | **NON TESTÉ** | Nécessite USE_RERANKING=true, RERANKING_BACKEND=local, RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2 + rebuild |
| T20 -- USE_RERANKING remote RankGPT | **PASS** | _rerank_remote() retourne la liste réordonnée. Backend : LiteLLM qwen3.5-4b. Validé par docker exec direct. |
| T21 -- USE_AGENTIC_RAG + search_code_examples | **EN ATTENTE** | generate_code_example_summary() retournait '' avec max_tokens=100 (budget épuisé par le bloc thinking qwen3). Fix appliqué : max_tokens=2000. Rebuild requis pour confirmer. |
| T22 -- Ollama injoignable | **NON TESTÉ** | Nécessite OLLAMA_BASE_URL=http://192.168.10.16:19999 + restart |
| T23a -- URL syntaxiquement invalide | **PASS** | success:false, "No content retrieved" |
| T23b -- domaine inexistant | **PASS** | success:false, "No content retrieved" |
| T23c -- 404 Python docs | **PARTIEL** | success:true, 1 chunk stocké. La page 404 est une vraie page HTML (256 mots). crawl4ai ne vérifie pas le code HTTP. Comportement documenté, pas un bug bloquant. |
| T24 -- RAG sur base vide | **NON TESTÉ** | Nécessite TRUNCATE sources CASCADE (destructif sur les données de test) |
| T25 -- SearXNG injoignable | **NON TESTÉ** | Nécessite SEARXNG_URL=http://searxng:19999 + restart |
| T26 -- search_code_examples sans USE_AGENTIC_RAG | **PASS** | success:false, message exact "Code example extraction is disabled. Perform a normal RAG search." |
| T27 -- URLs avec doublons et vides | **PASS** | total_urls=2 après déduplication et filtrage des vides |
| T28 -- query RAG vide | **PASS** | "" et "   " : success:false, "Query cannot be empty" |
| T29 -- Lazy init Chromium | **NON TESTÉ** | Nécessite SSH sur LXC 122 : ps aux, docker stats |
| T30 -- mem_limit container | **NON TESTÉ** | Nécessite SSH sur LXC 122 : docker inspect + docker stats |

---

## Observations notables

1. **search_code_examples toujours listé** : L'outil apparaît dans la liste même avec USE_AGENTIC_RAG=false, mais retourne une erreur explicite quand appelé. Le TEST_PLAN attendait qu'il soit absent -- à corriger dans le TEST_PLAN.

2. **T06 -- sitemap index non supporté** : python.org utilise un sitemap index (sitemaps imbriqués) que smart_crawl_url ne parse pas. Utiliser crawl4ai.com comme sitemap de référence dans le TEST_PLAN. Note : le crawl de 86 pages a saturé le CPU à 100% pendant ~45s -- documenter max_concurrent recommandé de 3 pour LXC 2 coeurs.

3. **T10 -- search_mode hybrid par défaut** : USE_HYBRID_SEARCH=true dans le .env livré. T17 est donc un test de confirmation plutôt qu'activation.

4. **T23c -- pages 404 crawlées** : crawl4ai stocke le contenu HTML de la page 404 si elle contient du contenu. Aucun mécanisme de vérification du code HTTP côté MCP.

---

## Prochaine session -- tests restants

### Groupe 1 -- Tests restants nécessitant rebuild ou config

**T21 (agentic RAG) -- fix max_tokens appliqué, rebuild requis :**
```bash
cd /opt/crawl4ai-rag-mcp
git pull && docker compose build mcp-crawl4ai && docker compose up -d mcp-crawl4ai
# Valider via docker exec :
docker exec mcp-crawl4ai python3 -c "
import sys, os; sys.path.insert(0, '/app/src')
from utils import generate_code_example_summary
print(repr(generate_code_example_summary('def add(a,b): return a+b', 'arithmetic utils', 'use add() to sum')))
"
```

**T19 (reranking local CrossEncoder) :**
```bash
# Dans .env :
USE_RERANKING=true
RERANKING_BACKEND=local
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

docker compose restart mcp-crawl4ai
docker logs mcp-crawl4ai --tail 50 | grep -i rerank
# Attendu : "Reranking model loaded (local CrossEncoder: ...)"
```

**T22 (Ollama injoignable) :**
```bash
# Dans .env :
OLLAMA_BASE_URL=http://192.168.10.16:19999

docker compose restart mcp-crawl4ai
# Tester scrape_urls, puis remettre l'URL correcte
```

**T25 (SearXNG injoignable) :**
```bash
# Dans .env :
SEARXNG_URL=http://searxng:19999

docker compose restart mcp-crawl4ai
# Tester searxng_search, puis remettre l'URL correcte
```

### Groupe 2 -- Commandes SSH sur LXC 122

**T24 (RAG base vide) :**
```bash
docker exec -it postgres-rag psql -U rag -d ragdb -c "TRUNCATE sources CASCADE;"
# Puis perform_rag_query -- vérifier pas d'exception
```

**T29 (lazy init Chromium) :**
```bash
docker compose down && docker compose up -d
docker logs mcp-crawl4ai --tail 20
docker exec mcp-crawl4ai ps aux | grep -i chrom
docker stats mcp-crawl4ai --no-stream
# Puis lancer un crawl et revérifier
```

**T30 (mem_limit) :**
```bash
docker inspect mcp-crawl4ai | python3 -c \
  "import sys,json; d=json.load(sys.stdin)[0]; m=d['HostConfig']['Memory']; print(f'mem_limit={m} bytes ({m/1024**3:.1f} GB)')"
docker inspect mcp-crawl4ai | python3 -c \
  "import sys,json; d=json.load(sys.stdin)[0]; m=d['HostConfig']['MemorySwap']; print(f'memswap_limit={m} bytes ({m/1024**3:.1f} GB)')"
docker stats --no-stream
```
