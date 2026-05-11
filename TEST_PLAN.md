# Plan de Test -- crawl4ai-rag-mcp

**Version :** 1.0  
**Date :** 2026-05-11  
**Serveur cible :** LXC 122 -- 192.168.10.22, port 8051  
**Transport :** SSE (Server-Sent Events)  
**Stack :** PostgreSQL+pgvector, Ollama externe (192.168.10.16:11434), SearXNG

---

## Table des matières

1. [Prérequis généraux](#prérequis-généraux)
2. [Conventions](#conventions)
3. [T01 -- Vérification de connectivité SSE](#t01----vérification-de-connectivité-sse)
4. [T02 -- scrape_urls : URL unique](#t02----scrape_urls--url-unique)
5. [T03 -- scrape_urls : liste d'URLs (batch)](#t03----scrape_urls--liste-durls-batch)
6. [T04 -- scrape_urls : mode return_raw_markdown](#t04----scrape_urls--mode-return_raw_markdown)
7. [T05 -- smart_crawl_url : page web récursive](#t05----smart_crawl_url--page-web-récursive)
8. [T06 -- smart_crawl_url : sitemap.xml](#t06----smart_crawl_url--sitemapxml)
9. [T07 -- smart_crawl_url : fichier llms.txt](#t07----smart_crawl_url--fichier-llmstxt)
10. [T08 -- get_available_sources : état nominal](#t08----get_available_sources--état-nominal)
11. [T09 -- get_available_sources : base vide](#t09----get_available_sources--base-vide)
12. [T10 -- perform_rag_query : recherche sans filtre source](#t10----perform_rag_query--recherche-sans-filtre-source)
13. [T11 -- perform_rag_query : recherche avec filtre source](#t11----perform_rag_query--recherche-avec-filtre-source)
14. [T12 -- searxng_search : recherche générale](#t12----searxng_search--recherche-générale)
15. [T13 -- searxng_images : recherche images](#t13----searxng_images--recherche-images)
16. [T14 -- searxng_news : recherche actualités](#t14----searxng_news--recherche-actualités)
17. [T15 -- search : outil composite (SearXNG + scrape + RAG)](#t15----search--outil-composite-searxng--scrape--rag)
18. [T16 -- search : mode return_raw_markdown](#t16----search--mode-return_raw_markdown)
19. [T17 -- Feature : USE_HYBRID_SEARCH](#t17----feature--use_hybrid_search)
20. [T18 -- Feature : USE_CONTEXTUAL_EMBEDDINGS](#t18----feature--use_contextual_embeddings)
21. [T19 -- Feature : USE_RERANKING (backend local CrossEncoder)](#t19----feature--use_reranking-backend-local-crossencoder)
22. [T20 -- Feature : USE_RERANKING (backend remote RankGPT)](#t20----feature--use_reranking-backend-remote-rankgpt)
23. [T21 -- Feature : USE_AGENTIC_RAG + search_code_examples](#t21----feature--use_agentic_rag--search_code_examples)
24. [T22 -- Erreur : Ollama injoignable](#t22----erreur--ollama-injoignable)
25. [T23 -- Erreur : URL invalide ou inaccessible](#t23----erreur--url-invalide-ou-inaccessible)
26. [T24 -- Erreur : base de données vide (RAG sans contenu)](#t24----erreur--base-de-données-vide-rag-sans-contenu)
27. [T25 -- Erreur : SearXNG injoignable](#t25----erreur--searxng-injoignable)
28. [T26 -- Erreur : search_code_examples sans USE_AGENTIC_RAG](#t26----erreur--search_code_examples-sans-use_agentic_rag)
29. [T27 -- Robustesse : liste d'URLs avec doublons et URLs vides](#t27----robustesse--liste-durls-avec-doublons-et-urls-vides)
30. [T28 -- Robustesse : query RAG vide](#t28----robustesse--query-rag-vide)

---

## Prérequis généraux

Avant d'exécuter tout scénario :

1. Le stack Docker est démarré sur LXC 122 :
   ```bash
   ssh root@192.168.10.22
   cd /path/to/crawl4ai-rag-mcp
   docker compose ps
   # Attendu : mcp-crawl4ai, postgres-rag, searxng, valkey, caddy -- tous Up
   ```

2. Claude Code est configuré pour se connecter au serveur MCP via SSE :
   ```json
   {
     "mcpServers": {
       "crawl4ai": {
         "url": "http://192.168.10.22:8051/sse"
       }
     }
   }
   ```

3. Le fichier `.env` est présent dans le répertoire du projet avec au minimum :
   ```env
   DATABASE_URL=postgresql://rag:rag@postgres:5432/ragdb
   OLLAMA_BASE_URL=http://192.168.10.16:11434
   EMBEDDING_MODEL=nomic-embed-text:v1.5
   EMBEDDING_DIMENSIONS=768
   SEARXNG_URL=http://searxng:8080
   ```

4. Ollama est accessible depuis LXC 122 :
   ```bash
   curl http://192.168.10.16:11434/api/tags
   # Attendu : JSON avec la liste des modèles incluant nomic-embed-text:v1.5
   ```

5. La base de données est initialisée (tables `sources`, `crawled_pages`, `code_examples` et fonctions `match_crawled_pages`, `match_code_examples` créées depuis `crawled_pages.sql`).

---

## Conventions

- **Étapes** : formulées comme des instructions à donner à Claude Code dans une conversation.
- **Redémarrage** : toute modification du `.env` nécessite un redémarrage du conteneur `mcp-crawl4ai` :
  ```bash
  docker compose restart mcp-crawl4ai
  ```
- **Vérification des logs** :
  ```bash
  docker logs mcp-crawl4ai --tail 50
  ```
- **Réinitialisation de la base** (pour les tests nécessitant une base vide) :
  ```bash
  docker exec -it postgres-rag psql -U rag -d ragdb -c "TRUNCATE sources CASCADE;"
  ```
- **Pass/Fail** : le critère est binaire. Un résultat partiel qui retourne `"success": false` est un Fail même si la réponse JSON est bien formée, sauf indication contraire.

---

## T01 -- Vérification de connectivité SSE

**Objectif :** Confirmer que le serveur MCP répond et que Claude Code peut lister les outils disponibles.

**Prérequis :** Stack Docker démarré, Claude Code configuré.

**Configuration `.env` requise :** Configuration minimale (voir Prérequis généraux).

**Étapes :**

1. Dans une conversation Claude Code, saisir : "Quels outils MCP sont disponibles depuis le serveur crawl4ai ?"
2. Observer la liste des outils retournée.

**Résultat attendu :**

Claude Code liste au minimum les outils suivants : `scrape_urls`, `smart_crawl_url`, `get_available_sources`, `perform_rag_query`, `search`, `searxng_search`, `searxng_images`, `searxng_news`. L'outil `search_code_examples` n'apparaît pas (USE_AGENTIC_RAG désactivé par défaut).

**Critère Pass :** Tous les 8 outils core sont listés, pas d'erreur de connexion.  
**Critère Fail :** Timeout, erreur de connexion SSE, ou liste vide.

---

## T02 -- scrape_urls : URL unique

**Objectif :** Scraper une URL et vérifier le stockage en base de données.

**Prérequis :** T01 passé. Ollama accessible.

**Configuration `.env` requise :**
```env
USE_CONTEXTUAL_EMBEDDINGS=false
USE_AGENTIC_RAG=false
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil scrape_urls pour scraper l'URL https://docs.python.org/3/library/json.html et stocker son contenu."
2. Vérifier la réponse JSON retournée.
3. Vérifier en base :
   ```bash
   docker exec -it postgres-rag psql -U rag -d ragdb -c \
     "SELECT url, COUNT(*) as chunks FROM crawled_pages WHERE url LIKE '%json%' GROUP BY url;"
   ```

**Résultat attendu :**

- Réponse JSON : `"success": true`, champ `chunks_stored` > 0, champ `source_id` = `"docs.python.org"`.
- En base : au moins 1 ligne avec url contenant `json.html`, plusieurs chunks.

**Critère Pass :** `success: true`, `chunks_stored >= 1`, enregistrement confirmé en base.  
**Critère Fail :** `success: false`, `chunks_stored = 0`, ou erreur de connexion Ollama dans les logs.

---

## T03 -- scrape_urls : liste d'URLs (batch)

**Objectif :** Scraper plusieurs URLs en un seul appel et vérifier la réponse multi-URL.

**Prérequis :** T02 passé.

**Configuration `.env` requise :**
```env
USE_CONTEXTUAL_EMBEDDINGS=false
USE_AGENTIC_RAG=false
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil scrape_urls avec la liste d'URLs suivante : ['https://docs.python.org/3/library/os.html', 'https://docs.python.org/3/library/sys.html', 'https://docs.python.org/3/library/pathlib.html']"
2. Vérifier la réponse JSON.
3. Vérifier en base :
   ```bash
   docker exec -it postgres-rag psql -U rag -d ragdb -c \
     "SELECT source_id, COUNT(DISTINCT url) as urls, COUNT(*) as chunks FROM crawled_pages GROUP BY source_id;"
   ```

**Résultat attendu :**

- Réponse JSON : `"success": true`, mode `"multi_url"`, `summary.total_urls = 3`, `summary.successful_urls >= 2`.
- `results` est un tableau avec une entrée par URL.
- En base : 3 URLs distinctes stockées sous `source_id = "docs.python.org"`.

**Critère Pass :** Au moins 2 URLs scrappées avec succès, format multi_url présent.  
**Critère Fail :** `successful_urls = 0`, ou réponse au format single URL (régression de format).

---

## T04 -- scrape_urls : mode return_raw_markdown

**Objectif :** Vérifier que le mode `return_raw_markdown=true` retourne le contenu brut sans le stocker.

**Prérequis :** T01 passé. Une URL accessible.

**Configuration `.env` requise :** Configuration minimale.

**Étapes :**

1. Compter les chunks en base avant le test :
   ```bash
   docker exec -it postgres-rag psql -U rag -d ragdb -c \
     "SELECT COUNT(*) FROM crawled_pages WHERE url = 'https://docs.python.org/3/library/json.html';"
   ```
2. Dans Claude Code, demander : "Utilise l'outil scrape_urls avec url='https://docs.python.org/3/library/json.html' et return_raw_markdown=true. Montre-moi les 200 premiers caractères du contenu retourné."
3. Recompter les chunks en base après le test (même requête qu'en étape 1).

**Résultat attendu :**

- La réponse contient du markdown brut visible dans la réponse (texte HTML converti).
- Le mode est `"raw_markdown"` dans le JSON.
- Le nombre de chunks en base est identique avant et après (aucun stockage).

**Critère Pass :** Contenu markdown retourné, comptage en base inchangé.  
**Critère Fail :** Contenu vide, ou nouveaux chunks créés en base.

---

## T05 -- smart_crawl_url : page web récursive

**Objectif :** Vérifier le crawl récursif d'une page web avec suivi des liens internes.

**Prérequis :** T02 passé.

**Configuration `.env` requise :**
```env
USE_AGENTIC_RAG=false
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil smart_crawl_url pour crawler https://docs.python.org/3/library/json.html avec max_depth=1 et max_concurrent=3."
2. Observer le `crawl_type` dans la réponse.
3. Vérifier en base :
   ```bash
   docker exec -it postgres-rag psql -U rag -d ragdb -c \
     "SELECT COUNT(DISTINCT url) FROM crawled_pages WHERE source_id = 'docs.python.org';"
   ```

**Résultat attendu :**

- `crawl_type = "webpage"`.
- `pages_crawled >= 1`, `chunks_stored > 0`.
- `sources_updated >= 1` (la table `sources` est mise à jour avec un résumé).
- En base : plusieurs URLs distinctes de `docs.python.org`.

**Critère Pass :** `crawl_type = "webpage"`, `pages_crawled >= 1`, source créée en base.  
**Critère Fail :** `"success": false`, `pages_crawled = 0`.

---

## T06 -- smart_crawl_url : sitemap.xml

**Objectif :** Vérifier la détection et le traitement d'un sitemap XML.

**Prérequis :** T02 passé. Choisir un site avec un sitemap accessible et de taille raisonnable.

**Configuration `.env` requise :**
```env
USE_AGENTIC_RAG=false
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil smart_crawl_url avec url='https://www.python.org/sitemap.xml' et max_concurrent=5. Indique-moi le crawl_type et le nombre de pages crawlées."
2. Observer la réponse.

**Résultat attendu :**

- `crawl_type = "sitemap"`.
- `pages_crawled > 0`.
- `urls_crawled` liste des URLs du sitemap.

**Critère Pass :** `crawl_type = "sitemap"`, `pages_crawled >= 1`.  
**Critère Fail :** `crawl_type != "sitemap"`, ou erreur "No URLs found in sitemap".

---

## T07 -- smart_crawl_url : fichier llms.txt

**Objectif :** Vérifier la détection et le traitement d'un fichier texte (llms.txt ou autre `.txt`).

**Prérequis :** T02 passé.

**Configuration `.env` requise :** Configuration minimale.

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil smart_crawl_url avec url='https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/README.md' -- attends, utilise plutôt une URL se terminant par .txt. Utilise l'URL 'https://llmstxt.org/llms.txt' avec smart_crawl_url."
2. Observer le `crawl_type` retourné.

**Résultat attendu :**

- `crawl_type = "text_file"`.
- `pages_crawled >= 1`, `chunks_stored > 0`.

**Critère Pass :** `crawl_type = "text_file"`, contenu stocké.  
**Critère Fail :** `crawl_type != "text_file"`, ou `success: false`.

---

## T08 -- get_available_sources : état nominal

**Objectif :** Lister les sources disponibles après avoir stocké du contenu.

**Prérequis :** T02 ou T05 passé (au moins un crawl effectué).

**Configuration `.env` requise :** Configuration minimale.

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil get_available_sources et liste toutes les sources disponibles avec leur résumé."

**Résultat attendu :**

- `"success": true`.
- `count >= 1`.
- Chaque source contient : `source_id`, `summary`, `total_words`, `created_at`, `updated_at`.
- `docs.python.org` est présent dans la liste si T02 a été exécuté.

**Critère Pass :** `success: true`, `count >= 1`, `source_id` non vide pour chaque entrée.  
**Critère Fail :** `success: false`, ou liste vide alors que du contenu a été stocké.

---

## T09 -- get_available_sources : base vide

**Objectif :** Vérifier le comportement sur base vide (pas d'erreur, liste vide).

**Prérequis :** Base réinitialisée.

**Configuration `.env` requise :** Configuration minimale.

**Préparation :**
```bash
docker exec -it postgres-rag psql -U rag -d ragdb -c "TRUNCATE sources CASCADE;"
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil get_available_sources."

**Résultat attendu :**

- `"success": true`.
- `count = 0`.
- `sources = []`.
- Pas d'exception ni d'erreur.

**Critère Pass :** Réponse JSON valide avec `success: true` et liste vide.  
**Critère Fail :** `success: false`, exception Python, ou timeout.

---

## T10 -- perform_rag_query : recherche sans filtre source

**Objectif :** Effectuer une recherche sémantique sans filtre source et vérifier la pertinence des résultats.

**Prérequis :** T02 passé (contenu de docs.python.org en base).

**Configuration `.env` requise :**
```env
USE_HYBRID_SEARCH=false
USE_RERANKING=false
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil perform_rag_query avec query='how to serialize Python objects to JSON' et match_count=5. Montre-moi les URLs et scores de similarité des résultats."

**Résultat attendu :**

- `"success": true`.
- `count >= 1`.
- `search_mode = "vector"`.
- `reranking_applied = false`.
- Les résultats contiennent des chunks pertinents (mention de `json.dumps`, `json.loads`, etc.).
- Chaque résultat a un champ `similarity` entre 0 et 1.

**Critère Pass :** `success: true`, `count >= 1`, résultats sémantiquement liés à JSON.  
**Critère Fail :** `success: false`, `count = 0`, ou timeout.

---

## T11 -- perform_rag_query : recherche avec filtre source

**Objectif :** Vérifier que le filtre `source` restreint les résultats au domaine spécifié.

**Prérequis :** T02 et T05 passés (au moins deux sources différentes en base).

**Configuration `.env` requise :**
```env
USE_HYBRID_SEARCH=false
USE_RERANKING=false
```

**Étapes :**

1. Dans Claude Code, demander : "D'abord utilise get_available_sources pour lister les sources. Ensuite, utilise perform_rag_query avec query='file path operations', source='docs.python.org', match_count=5."
2. Vérifier que tous les résultats proviennent bien de `docs.python.org`.

**Résultat attendu :**

- `source_filter = "docs.python.org"`.
- Tous les champs `url` dans les résultats contiennent `docs.python.org`.
- Pas de résultats provenant d'autres domaines.

**Critère Pass :** 100% des résultats filtres sur le bon `source_id`.  
**Critère Fail :** Résultats d'autres domaines présents, ou `count = 0` alors que le domaine contient du contenu pertinent.

---

## T12 -- searxng_search : recherche générale

**Objectif :** Vérifier que searxng_search retourne des résultats web bruts sans scraping ni RAG.

**Prérequis :** T01 passé. SearXNG accessible depuis le conteneur `mcp-crawl4ai`.

**Configuration `.env` requise :**
```env
SEARXNG_URL=http://searxng:8080
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil searxng_search avec query='Python asyncio tutorial', num_results=5."
2. Observer les champs retournés.

**Résultat attendu :**

- `"success": true`.
- `count >= 1`.
- Chaque résultat contient : `title`, `url`, `snippet`, `engine`.
- Aucun embedding Ollama sollicité (pas d'appel à Ollama dans les logs).
- Les URLs ne sont pas stockées en base (vérifiable avec `SELECT COUNT(*) FROM crawled_pages WHERE url LIKE '%asyncio%'`).

**Critère Pass :** `success: true`, `count >= 1`, pas de stockage en base.  
**Critère Fail :** `success: false`, ou connexion SearXNG échouée.

---

## T13 -- searxng_images : recherche images

**Objectif :** Vérifier que searxng_images retourne des URLs d'images avec thumbnails.

**Prérequis :** T12 passé.

**Configuration `.env` requise :** Identique à T12.

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil searxng_images avec query='Python logo', num_results=5."

**Résultat attendu :**

- `"success": true`.
- Chaque résultat contient : `title`, `url`, `thumbnail_src`, `source`.
- Les URLs pointent vers des images (extensions `.jpg`, `.png`, `.svg`, etc. ou domaines d'hébergement d'images).

**Critère Pass :** `success: true`, résultats avec champ `thumbnail_src` présent (peut être vide si l'engine ne fournit pas de thumbnail).  
**Critère Fail :** `success: false`, ou `count = 0`.

---

## T14 -- searxng_news : recherche actualités

**Objectif :** Vérifier que searxng_news retourne des articles d'actualité avec date de publication.

**Prérequis :** T12 passé.

**Configuration `.env` requise :** Identique à T12.

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil searxng_news avec query='artificial intelligence', num_results=5."

**Résultat attendu :**

- `"success": true`.
- Chaque résultat contient : `title`, `url`, `snippet`, `publishedDate`, `engine`.
- `publishedDate` est renseigné pour au moins certains résultats.

**Critère Pass :** `success: true`, `count >= 1`, champ `publishedDate` présent.  
**Critère Fail :** `success: false`, ou `count = 0`.

---

## T15 -- search : outil composite (SearXNG + scrape + RAG)

**Objectif :** Vérifier le workflow complet : SearXNG --> scrape --> embedding --> RAG par URL.

**Prérequis :** T02, T12 passés. Ollama accessible.

**Configuration `.env` requise :**
```env
SEARXNG_URL=http://searxng:8080
USE_HYBRID_SEARCH=false
USE_RERANKING=false
USE_CONTEXTUAL_EMBEDDINGS=false
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil search avec query='Python dataclass tutorial', num_results=3, max_rag_workers=3."
2. Observer la réponse complète.

**Résultat attendu :**

- `"success": true`.
- `mode = "rag_query"`.
- `searxng_results` contient 3 URLs.
- `results` est un dictionnaire URL --> liste de résultats RAG (ou message d'erreur par URL si scraping échoue).
- `summary.urls_found >= 1`, `summary.urls_scraped >= 1`, `summary.urls_processed >= 1`.
- `summary.processing_time_seconds` est renseigné.

**Critère Pass :** `success: true`, au moins 1 URL avec résultats RAG non vides.  
**Critère Fail :** `success: false`, ou toutes les URLs retournent "No relevant results".

---

## T16 -- search : mode return_raw_markdown

**Objectif :** Vérifier que le mode `return_raw_markdown=true` dans `search` retourne le markdown sans RAG.

**Prérequis :** T12 passé.

**Configuration `.env` requise :** Identique à T15.

**Étapes :**

1. Dans Claude Code, demander : "Utilise l'outil search avec query='Python dict comprehension', num_results=2, return_raw_markdown=true."
2. Observer la structure de la réponse.

**Résultat attendu :**

- `"success": true`.
- `mode = "raw_markdown"`.
- `results` est un dictionnaire URL --> texte markdown brut (non un tableau de chunks avec scores).
- Pas d'appel à `perform_rag_query` dans le traitement (visible dans les logs ou dans la structure de réponse).

**Critère Pass :** `success: true`, `mode = "raw_markdown"`, contenu markdown présent pour au moins 1 URL.  
**Critère Fail :** `mode = "rag_query"` au lieu de `raw_markdown`, ou contenu vide.

---

## T17 -- Feature : USE_HYBRID_SEARCH

**Objectif :** Vérifier que la recherche hybride (vectorielle + keyword ILIKE) est activée et retourne un `search_mode` correct.

**Prérequis :** T02 passé (contenu en base).

**Configuration `.env` requise :**
```env
USE_HYBRID_SEARCH=true
USE_RERANKING=false
```

**Préparation :**
```bash
# Modifier .env
# Redémarrer le conteneur
docker compose restart mcp-crawl4ai
docker logs mcp-crawl4ai --tail 20
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise perform_rag_query avec query='json dumps serialization' et match_count=5."
2. Observer le champ `search_mode` dans la réponse.

**Résultat attendu :**

- `search_mode = "hybrid"`.
- `"success": true`, `count >= 1`.
- Les résultats incluent des chunks correspondant à la fois à la similarité vectorielle et à la présence littérale du mot "json" ou "dumps".

**Critère Pass :** `search_mode = "hybrid"`, résultats retournés.  
**Critère Fail :** `search_mode = "vector"` (hybrid non activé), ou `success: false`.

**Nettoyage :** Remettre `USE_HYBRID_SEARCH=false` et redémarrer si les tests suivants ne requièrent pas cette feature.

---

## T18 -- Feature : USE_CONTEXTUAL_EMBEDDINGS

**Objectif :** Vérifier que les embeddings contextuels sont générés lors du stockage (chaque chunk est enrichi d'un contexte LLM avant embedding).

**Prérequis :** Ollama accessible avec un modèle de chat disponible. Contenu à crawler.

**Configuration `.env` requise :**
```env
USE_CONTEXTUAL_EMBEDDINGS=true
MODEL_CHOICE=qwen3:4b
OLLAMA_BASE_URL=http://192.168.10.16:11434
```

**Préparation :**
```bash
# Vérifier que le modèle est disponible sur Ollama
curl http://192.168.10.16:11434/api/tags | python3 -c "import sys,json; models=[m['name'] for m in json.load(sys.stdin)['models']]; print([m for m in models if 'qwen' in m.lower()])"
# Modifier .env, redémarrer
docker compose restart mcp-crawl4ai
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise scrape_urls pour scraper https://docs.python.org/3/library/functools.html."
2. Observer les logs du conteneur pendant et après le crawl :
   ```bash
   docker logs mcp-crawl4ai --tail 100 | grep -i contextual
   ```

**Résultat attendu :**

- Dans les logs : `Use contextual embeddings: True` suivi de tentatives de génération de contexte via `chat.completions.create`.
- La réponse de `scrape_urls` retourne `success: true` et `chunks_stored > 0`.
- Les métadonnées en base contiennent `contextual_embedding: true` pour certains chunks :
  ```bash
  docker exec -it postgres-rag psql -U rag -d ragdb -c \
    "SELECT metadata FROM crawled_pages WHERE url LIKE '%functools%' LIMIT 3;"
  ```

**Critère Pass :** Log `Use contextual embeddings: True` présent, chunks stockés avec succès.  
**Critère Fail :** Log `Use contextual embeddings: False`, ou erreur de connexion au modèle de chat.

**Avertissement :** Ce test est significativement plus lent que les autres (appel LLM par chunk). Utiliser une page courte pour réduire le temps d'exécution.

**Nettoyage :** Remettre `USE_CONTEXTUAL_EMBEDDINGS=false` après le test.

---

## T19 -- Feature : USE_RERANKING (backend local CrossEncoder)

**Objectif :** Vérifier que le reranking CrossEncoder local est chargé au démarrage et appliqué aux résultats RAG.

**Prérequis :** T02 passé. Le modèle CrossEncoder doit pouvoir être téléchargé depuis HuggingFace (ou être en cache).

**Configuration `.env` requise :**
```env
USE_RERANKING=true
RERANKING_BACKEND=local
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
USE_HYBRID_SEARCH=false
```

**Préparation :**
```bash
docker compose restart mcp-crawl4ai
# Attendre le démarrage (le modèle est chargé au boot)
docker logs mcp-crawl4ai --tail 50 | grep -i rerank
# Attendu : "Reranking model loaded (local CrossEncoder: cross-encoder/ms-marco-MiniLM-L-6-v2)"
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise perform_rag_query avec query='serialize object json python' et match_count=5. Montre-moi les scores de reranking si présents."

**Résultat attendu :**

- `reranking_applied = true`.
- Chaque résultat contient un champ `rerank_score` (float).
- Les résultats sont triés par `rerank_score` décroissant.
- Log visible : `Applying reranking (backend=local)...` suivi de `Reranking completed`.

**Critère Pass :** `reranking_applied = true`, champ `rerank_score` présent dans les résultats.  
**Critère Fail :** `reranking_applied = false`, ou absence du champ `rerank_score`.

**Nettoyage :** Remettre `USE_RERANKING=false` après le test.

---

## T20 -- Feature : USE_RERANKING (backend remote RankGPT)

**Objectif :** Vérifier que le reranking via LLM Ollama (RankGPT) réordonne les résultats.

**Prérequis :** T02 passé. Ollama accessible avec un modèle de chat disponible.

**Configuration `.env` requise :**
```env
USE_RERANKING=true
RERANKING_BACKEND=remote
RERANKING_MODEL=qwen3:4b
OLLAMA_BASE_URL=http://192.168.10.16:11434
USE_HYBRID_SEARCH=false
```

**Préparation :**
```bash
docker compose restart mcp-crawl4ai
docker logs mcp-crawl4ai --tail 20
# Attendu : PAS de "Reranking model loaded (local CrossEncoder...)" -- mode remote ne charge rien au boot
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise perform_rag_query avec query='Python exception handling best practices' et match_count=5."
2. Observer les logs pendant l'exécution :
   ```bash
   docker logs mcp-crawl4ai --tail 30
   ```

**Résultat attendu :**

- `reranking_applied = true`.
- Dans les logs : `Applying reranking (backend=remote)...` suivi de `Reranking completed`.
- L'ordre des résultats est potentiellement différent de l'ordre vectoriel brut.
- Note : le champ `rerank_score` n'est pas présent en mode remote (RankGPT réordonne par position, pas par score numérique).

**Critère Pass :** `reranking_applied = true`, pas d'erreur remote reranking dans les logs.  
**Critère Fail :** `reranking_applied = false`, ou log `Error during remote reranking`.

**Nettoyage :** Remettre `USE_RERANKING=false` après le test.

---

## T21 -- Feature : USE_AGENTIC_RAG + search_code_examples

**Objectif :** Vérifier que les blocs de code sont extraits et indexés séparément lors du crawl, et que `search_code_examples` les retrouve.

**Prérequis :** Ollama accessible avec un modèle de chat (pour la génération des résumés de code). Contenu avec des blocs de code à crawler.

**Configuration `.env` requise :**
```env
USE_AGENTIC_RAG=true
MODEL_CHOICE=qwen3:4b
OLLAMA_BASE_URL=http://192.168.10.16:11434
```

**Préparation :**
```bash
docker compose restart mcp-crawl4ai
# Vérifier que search_code_examples apparaît dans la liste des outils
```

**Étapes :**

1. Dans Claude Code, demander : "Quels outils MCP sont disponibles ? Est-ce que search_code_examples est disponible ?"
2. Demander : "Utilise smart_crawl_url pour crawler https://docs.python.org/3/library/json.html (max_depth=1)."
3. Vérifier la table `code_examples` en base :
   ```bash
   docker exec -it postgres-rag psql -U rag -d ragdb -c \
     "SELECT COUNT(*) FROM code_examples WHERE source_id = 'docs.python.org';"
   ```
4. Demander : "Utilise search_code_examples avec query='json.dumps example with indent' et match_count=3."

**Résultat attendu :**

- Étape 1 : `search_code_examples` est listé.
- Étape 2 : La réponse de `smart_crawl_url` contient `code_examples_stored > 0`.
- Étape 3 : Au moins 1 ligne dans `code_examples`.
- Étape 4 : `success: true`, résultats contenant des champs `code`, `summary`, `similarity`.

**Critère Pass :** `search_code_examples` disponible, code extrait en base, résultats retournés.  
**Critère Fail :** Outil non disponible malgré `USE_AGENTIC_RAG=true`, ou `code_examples_stored = 0`.

**Nettoyage :** Remettre `USE_AGENTIC_RAG=false` après le test.

---

## T22 -- Erreur : Ollama injoignable

**Objectif :** Vérifier le comportement lors d'une panne Ollama (embedding impossible).

**Prérequis :** T01 passé.

**Configuration `.env` requise :**
```env
OLLAMA_BASE_URL=http://192.168.10.16:19999
# Port intentionnellement incorrect pour simuler une panne
```

**Préparation :**
```bash
# Modifier OLLAMA_BASE_URL avec un port invalide
docker compose restart mcp-crawl4ai
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise scrape_urls avec url='https://docs.python.org/3/library/json.html'."
2. Observer la réponse.
3. Demander : "Utilise perform_rag_query avec query='json serialization'."
4. Observer la réponse.

**Résultat attendu :**

- `scrape_urls` : Le crawl peut réussir (le navigateur ne dépend pas d'Ollama) mais le stockage en base échoue ou les embeddings sont des vecteurs zéro. Réponse avec `success: false` ou chunks avec embeddings nuls.
- `perform_rag_query` : `success: false` avec message d'erreur explicite indiquant l'échec d'embedding ou de connexion.
- Pas de crash du serveur MCP (le serveur continue de répondre).

**Critère Pass :** Réponse JSON avec `success: false` et message d'erreur, serveur MCP toujours accessible après.  
**Critère Fail :** Crash du serveur MCP (connexion SSE perdue), ou timeout sans réponse.

**Nettoyage :** Remettre `OLLAMA_BASE_URL=http://192.168.10.16:11434` et redémarrer.

---

## T23 -- Erreur : URL invalide ou inaccessible

**Objectif :** Vérifier la gestion des URLs invalides ou inaccessibles dans `scrape_urls`.

**Prérequis :** T01 passé.

**Configuration `.env` requise :** Configuration minimale.

**Sous-cas 23a -- URL syntaxiquement invalide :**

1. Dans Claude Code, demander : "Utilise scrape_urls avec url='not-a-valid-url'."
2. Résultat attendu : `success: false`, message d'erreur explicite sur l'URL invalide.

**Sous-cas 23b -- URL avec domaine inexistant :**

1. Dans Claude Code, demander : "Utilise scrape_urls avec url='https://this-domain-does-not-exist-xyz123.com/page'."
2. Résultat attendu : `success: false`, erreur de résolution DNS ou de connexion.

**Sous-cas 23c -- URL accessible mais retournant 404 :**

1. Dans Claude Code, demander : "Utilise scrape_urls avec url='https://docs.python.org/3/this-page-does-not-exist-404.html'."
2. Résultat attendu : `success: false` ou `chunks_stored = 0` (la page 404 peut contenir du contenu HTML minimal).

**Critère Pass :** Dans tous les cas, réponse JSON avec `success: false`, serveur MCP toujours opérationnel après.  
**Critère Fail :** Exception non gérée, crash du serveur, ou timeout.

---

## T24 -- Erreur : base de données vide (RAG sans contenu)

**Objectif :** Vérifier le comportement de `perform_rag_query` sur une base sans contenu.

**Prérequis :** T01 passé.

**Configuration `.env` requise :** Configuration minimale.

**Préparation :**
```bash
docker exec -it postgres-rag psql -U rag -d ragdb -c "TRUNCATE sources CASCADE;"
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise perform_rag_query avec query='how to use Python dict' et match_count=5."

**Résultat attendu :**

- Réponse JSON valide.
- `success: true` avec `count = 0` et `results = []`, OU `success: false` avec un message clair indiquant l'absence de résultats.
- Pas de crash ou timeout.

**Critère Pass :** Réponse JSON sans exception, serveur opérationnel.  
**Critère Fail :** Exception non gérée, timeout, ou erreur SQL non interceptée dans la réponse.

---

## T25 -- Erreur : SearXNG injoignable

**Objectif :** Vérifier la gestion d'une panne SearXNG dans les outils qui en dépendent.

**Prérequis :** T01 passé.

**Configuration `.env` requise :**
```env
SEARXNG_URL=http://searxng:19999
# Port intentionnellement incorrect
```

**Préparation :**
```bash
docker compose restart mcp-crawl4ai
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise searxng_search avec query='Python tutorial'."
2. Demander : "Utilise search avec query='Python asyncio'."
3. Demander : "Utilise searxng_images avec query='Python logo'."

**Résultat attendu :**

- Toutes les requêtes retournent `success: false`.
- Le message d'erreur mentionne l'impossibilité de se connecter à SearXNG (ex: `"Cannot connect to SearXNG at http://searxng:19999"`).
- L'outil `scrape_urls` et `perform_rag_query` (qui ne dépendent pas de SearXNG) restent fonctionnels.

**Critère Pass :** `success: false` avec message explicite sur SearXNG, outils non-SearXNG toujours fonctionnels.  
**Critère Fail :** Crash du serveur MCP, ou timeout sans réponse.

**Nettoyage :** Remettre `SEARXNG_URL=http://searxng:8080` et redémarrer.

---

## T26 -- Erreur : search_code_examples sans USE_AGENTIC_RAG

**Objectif :** Vérifier que `search_code_examples` retourne une erreur explicite quand `USE_AGENTIC_RAG=false`.

**Prérequis :** T01 passé.

**Configuration `.env` requise :**
```env
USE_AGENTIC_RAG=false
```

**Étapes :**

1. Dans Claude Code, demander : "Utilise search_code_examples avec query='json example'."

**Résultat attendu :**

- `"success": false`.
- Message d'erreur : `"Code example extraction is disabled. Perform a normal RAG search."` (message exact retourné par le code).

**Critère Pass :** `success: false` avec message indiquant que la feature est désactivée.  
**Critère Fail :** L'outil n'est pas disponible du tout (pas listé -- ce qui est acceptable si USE_AGENTIC_RAG=false le masque), ou retourne des résultats comme si la feature était active.

**Note :** Selon l'implémentation SSE/FastMCP, l'outil peut être absent de la liste des outils disponibles lorsque `USE_AGENTIC_RAG=false`. Dans ce cas, vérifier simplement qu'il n'est pas listé, ce qui est également un Pass.

---

## T27 -- Robustesse : liste d'URLs avec doublons et URLs vides

**Objectif :** Vérifier la déduplication des URLs et le filtrage des entrées vides.

**Prérequis :** T01 passé.

**Configuration `.env` requise :** Configuration minimale.

**Étapes :**

1. Dans Claude Code, demander : "Utilise scrape_urls avec la liste d'URLs : ['https://docs.python.org/3/library/json.html', '', 'https://docs.python.org/3/library/json.html', 'https://docs.python.org/3/library/os.html', '   ']"

**Résultat attendu :**

- La liste dupliquée est dédupliquée : `https://docs.python.org/3/library/json.html` n'est traitée qu'une fois.
- Les entrées vides (`''`, `'   '`) sont ignorées.
- Le résultat traite effectivement 2 URLs distinctes.
- `summary.total_urls = 2` (après déduplication et filtrage).

**Critère Pass :** `success: true`, déduplication confirmée, pas d'erreur sur les entrées vides.  
**Critère Fail :** Erreur explicite sur les entrées vides, ou double traitement de la même URL.

---

## T28 -- Robustesse : query RAG vide

**Objectif :** Vérifier la validation de l'input sur `perform_rag_query` avec une requête vide.

**Prérequis :** T01 passé.

**Configuration `.env` requise :** Configuration minimale.

**Étapes :**

1. Dans Claude Code, demander : "Utilise perform_rag_query avec query='' (chaîne vide) et match_count=5."
2. Demander également : "Utilise perform_rag_query avec query='   ' (espaces seulement)."

**Résultat attendu :**

- Dans les deux cas : `"success": false`.
- Message d'erreur : `"Query cannot be empty"` (validation explicite dans le code).
- Pas d'appel Ollama déclenché (aucun embedding généré).

**Critère Pass :** `success: false` avec message de validation, pas d'appel Ollama dans les logs.  
**Critère Fail :** Exception non gérée, ou tentative d'embedding d'une chaîne vide.

---

## Matrice de couverture

| Outil / Feature | Scénarios couverts |
|---|---|
| `scrape_urls` (URL unique) | T02 |
| `scrape_urls` (batch multi-URL) | T03 |
| `scrape_urls` (raw_markdown) | T04 |
| `smart_crawl_url` (webpage récursif) | T05 |
| `smart_crawl_url` (sitemap) | T06 |
| `smart_crawl_url` (txt/llms.txt) | T07 |
| `get_available_sources` (nominal) | T08 |
| `get_available_sources` (base vide) | T09 |
| `perform_rag_query` (sans filtre) | T10 |
| `perform_rag_query` (avec filtre source) | T11 |
| `searxng_search` | T12 |
| `searxng_images` | T13 |
| `searxng_news` | T14 |
| `search` (mode RAG) | T15 |
| `search` (mode raw_markdown) | T16 |
| `search_code_examples` | T21, T26 |
| USE_HYBRID_SEARCH | T17 |
| USE_CONTEXTUAL_EMBEDDINGS | T18 |
| USE_RERANKING (local) | T19 |
| USE_RERANKING (remote) | T20 |
| USE_AGENTIC_RAG | T21 |
| Ollama injoignable | T22 |
| URL invalide / inaccessible | T23 |
| Base de données vide | T09, T24 |
| SearXNG injoignable | T25 |
| Robustesse inputs | T27, T28 |
| Connectivité SSE | T01 |
