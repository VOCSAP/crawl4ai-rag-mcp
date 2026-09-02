# CLAUDE.md -- crawl4ai-rag-mcp

## Aperçu projet

Serveur MCP exposant un pipeline RAG basé sur Crawl4AI :
- Crawl web (`scrape_urls`, `smart_crawl_url`)
- Stockage vectoriel Postgres (`pgvector`, dimension 1024 par défaut, configurable via `EMBEDDING_DIMENSIONS` + édition du schéma SQL)
- Recherche hybride + rerank LLM (`perform_rag_query`, `search_code_examples`)
- Recherche méta web via SearXNG (`searxng_search`, `searxng_images`, `searxng_news`, `search` composite)

Fork de `ToKiDoO/crawl4ai-rag-mcp` adapté pour un déploiement 100% local :
- **Supabase cloud -> Postgres + pgvector local** (psycopg2 sync, dimension 1024 par défaut, alignée sur `bge-m3`)
- **OpenAI -> Ollama externe** (client `openai` Python avec `base_url=OLLAMA_BASE_URL/v1`)
- **MCP SearXNG TypeScript externe -> 3 outils intégrés** dans le serveur Python

## Architecture du code

- **`src/utils.py`** : seule couche d'accès DB. Toutes les opérations Postgres passent par ce module. `crawl4ai_mcp.py` ne fait plus de SQL direct.
- **`src/crawl4ai_mcp.py`** : serveur FastMCP, outils exposés, lifespan crée la connexion psycopg2 unique (pas de pool, usage MCP non concurrent).
- **`crawled_pages.sql`** : schéma Postgres + fonctions `match_crawled_pages` / `match_code_examples` (similarité cosinus). RLS retiré (pas de PostgREST local).
- **Reranking tri-backend** : `RERANKING_BACKEND=local` charge `sentence-transformers` CrossEncoder CPU à la demande, `RERANKING_BACKEND=remote` appelle un LLM via `OLLAMA_BASE_URL` (RankGPT-style), `RERANKING_BACKEND=http` appelle un serveur HuggingFace TEI `/rerank` externe (`RERANKING_HTTP_URL`, cross-encoder type `bge-reranker-v2-m3`) sans charger de modèle dans le process.
- **Contextual embeddings** : appel LLM optionnel pendant `scrape_urls`/`smart_crawl_url` si `USE_CONTEXTUAL_EMBEDDINGS=true` et `MODEL_CHOICE` défini. Workers configurables, troncature du doc parent ajustée au context window du modèle.

## Variables d'environnement clés

| Variable | Rôle | Défaut |
|---|---|---|
| `OLLAMA_BASE_URL` | Endpoint LLM/embeddings (compatible OpenAI v1) | `http://localhost:11434` |
| `EMBEDDING_MODEL` | Modèle d'embedding | `bge-m3` |
| `EMBEDDING_DIMENSIONS` | Dimension du vecteur (doit matcher `vector(N)` dans `crawled_pages.sql`) | `1024` |
| `MODEL_CHOICE` | LLM pour contextual embeddings (vide = désactivé) | -- |
| `RERANKING_BACKEND` | `local` (CrossEncoder), `remote` (LLM via Ollama) ou `http` (TEI `/rerank` externe) | `local` |
| `RERANKING_MODEL` | HF ID pour local, nom Ollama pour remote, ignoré pour http (modèle fixé côté serveur) | -- |
| `RERANKING_HTTP_URL` | URL de l'endpoint TEI `/rerank` (requis si `RERANKING_BACKEND=http`) | -- |
| `RERANKING_TIMEOUT` | Timeout secondes pour rerank (LLM remote / HTTP) | `60` |
| `USE_HYBRID_SEARCH` | Active la combinaison vecteur + ILIKE | `true` |
| `USE_RERANKING` | Active l'étape de rerank | `false` |
| `USE_CONTEXTUAL_EMBEDDINGS` | Enrichit chaque chunk avec un contexte LLM | `false` |
| `USE_AGENTIC_RAG` | Active `search_code_examples` (sinon l'outil retourne une erreur explicite) | `false` |
| `USE_KNOWLEDGE_GRAPH` | Neo4j -- hors scope. Conditionne aussi l'*enregistrement* des 3 outils Neo4j (voir ci-dessous) | `false` |
| `DATABASE_URL` | DSN Postgres | aucun -- obligatoire, `_build_db_pool` lève sinon |
| `SEARXNG_URL` | Endpoint SearXNG interne Docker | `http://searxng:8080` |
| `OPENAI_API_KEY` | Valeur factice (`ollama`) ou master key LiteLLM | -- |

**Règle stricte :** ne JAMAIS hardcoder une IP, un nom de modèle ou une dimension dans le code. Toujours passer par les env vars ci-dessus.

## Coût en contexte des descriptions d'outils

`mcp` pousse la docstring d'un outil VERBATIM comme sa description (`func_doc = description or fn.__doc__`, `mcp/server/fastmcp/tools/base.py`). Aucun parsing n'a lieu : un bloc `Args:` n'alimente jamais les descriptions par paramètre du schéma JSON, c'est de la prose facturée à chaque client, à chaque session. Écrire les docstrings en conséquence : une première phrase qui dit quoi et quand, les arbitrages entre outils voisins, les contraintes non devinables, et rien qui soit déjà porté par le nom, le type ou le défaut d'un paramètre.

Les 3 outils Neo4j (`query_knowledge_graph`, `check_ai_script_hallucinations`, `parse_github_repository`) ne sont enregistrés que si `USE_KNOWLEDGE_GRAPH=true`, via le décorateur `_knowledge_graph_tool()`. Aucun service Neo4j n'existe dans `docker-compose.yml`, donc ils ne faisaient que retourner leur erreur « disabled » tout en coûtant leur description. Quand la variable est à `false`, la fonction non décorée reste liée dans le namespace du module : basculer la variable suffit à les réexposer. La valeur est lue à l'import, donc propager un changement de `.env` avec `docker compose up -d`, jamais `restart`.
