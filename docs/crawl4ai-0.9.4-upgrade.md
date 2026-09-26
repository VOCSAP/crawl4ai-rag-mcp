# Montée de version crawl4ai 0.9.0 -> 0.9.4

Date : 2026-09-26. Branche : `claude/crawl4ai-rag-mcp-deps-cgpcuv`.

## 1. Modifications

| Fichier | Changement | Pourquoi |
|---|---|---|
| `pyproject.toml` | `crawl4ai==0.9.0` -> `crawl4ai==0.9.4` | Dernière version publiée sur PyPI (2026-09-23), sans rupture de compatibilité annoncée entre 0.9.1 et 0.9.4. |
| `src/crawl4ai_mcp.py` (`get_markdown`, mode `fit`) | `PruningContentFilter` -> `PruningContentFilterLXML` | En 0.9.4, `PruningContentFilter` émet une `DeprecationWarning` à chaque instanciation. Le remplaçant prend les mêmes arguments et donne la même sortie, avec un parcours de l'arbre en O(N) au lieu d'un coût super-linéaire. |
| `uv.lock` | Régénéré avec `uv lock` | Le lockfile était périmé : il figeait encore `crawl4ai==0.6.2` et les paquets `supabase` d'avant le fork, sans rapport avec `pyproject.toml`. |
| `README.md` | Mention de version `v0.9.0` -> `v0.9.4` | Cohérence de la documentation. La mention « crawl4ai 0.9.0 convention » de `execute_js` reste correcte : elle date la convention, qui n'a pas changé. |

### Ce que la 0.9.4 change pour nous (0.9.1 à 0.9.4 cumulées)

- **Scripts de suppression d'overlay et de bandeaux de consentement** : les `setTimeout` inconditionnels sont retirés, et `<body>` n'est plus supprimé quand il porte une classe « popup ». On n'active pas ces options explicitement, donc l'effet attendu est nul ou marginal.
- **Timeout de visibilité du body** : il devient configurable via `body_visibility_timeout`. Un avertissement est maintenant émis même avec `verbose=False` quand la page ne devient jamais visible. On peut donc voir apparaître dans les logs des lignes `Body never became visible after ...ms` qui n'existaient pas avant. Ce n'est pas une erreur.
- **`lxml`** : la contrainte passe de `~=5.3` à `>=5.3,<7`.

### Ce qui ne nous concerne pas

Les correctifs de sécurité de la 0.9.3 (5 advisories) et de la 0.9.4 (3 advisories) visent le serveur Docker de crawl4ai (API REST, Playground), la frontière de configuration non fiable et le chemin `PDFContentScrapingStrategy`. Nous utilisons crawl4ai comme bibliothèque et n'exposons aucune de ces surfaces. Notre `generate_pdf` passe par `pdf=True`, qui rend une page en PDF via Chromium : ce n'est pas le scraping de PDF. Sont aussi hors de notre chemin d'exécution :

- la fuite du dispatcher en streaming : on appelle `arun_many` avec `stream=False` ;
- le timeout du mode HTTP : on utilise la stratégie navigateur ;
- les corrections du deep crawl BFS/BestFirst : on n'utilise pas `deep_crawl` ;
- les corrections sur `robots.txt` : on n'active pas `check_robots_txt` ;
- l'expansion `rowspan`/`colspan` : elle s'applique à `result.tables` (`table_extraction.py`), que nous ne lisons pas. Le markdown n'est pas touché. Vérifié localement : un `rowspan` donne toujours une ligne décalée dans `raw_markdown`, comme en 0.9.0 ;
- la conservation des attributs de tableau dans `html2text` : elle ne s'applique qu'avec `bypass_tables`, que nous n'activons pas ;
- les corrections propres à Windows et macOS.

## 2. Vérifications déjà faites (environnement cloud, hors Docker)

1. `uv sync` installe `crawl4ai 0.9.4`.
2. Suite `tests/` contre un Postgres 16 + pgvector local, schéma `crawled_pages.sql` chargé : **41 passed**. C'est le même résultat qu'avant la montée de version.
3. Filtre sur un HTML synthétique, via `DefaultMarkdownGenerator` :
   - `PruningContentFilterLXML` n'émet aucune `DeprecationWarning`, alors que `PruningContentFilter` en émet une ;
   - le `fit_markdown` est **identique** entre les deux filtres.
4. `src/crawl4ai_mcp.py` se parse sans erreur.

**Non vérifié** : aucun crawl avec un vrai Chromium. Le Chromium attendu par Playwright n'est pas disponible dans l'environnement cloud. Ce point est couvert par les tests de déploiement ci-dessous.

## 3. Point d'attention au build

Le `Dockerfile` installe avec `uv pip install --system -e .`, qui **ignore `uv.lock`**. L'image résout donc les dépendances au moment du build. Elle peut par exemple récupérer `lxml` 6.x ou une version de Playwright plus récente que celles du lockfile, qui fige `lxml 5.4.0` et `playwright 1.52.0`. C'est le comportement actuel, il n'est pas introduit par ce changement. En revanche, il faut relever les versions réellement installées dans l'image (test D2).

## 4. Tests à faire au déploiement

Reconstruire l'image, sans `restart` qui ne reconstruit pas :

```bash
docker compose build mcp-crawl4ai
docker compose up -d mcp-crawl4ai
```

### D1. Démarrage et santé

```bash
docker compose ps mcp-crawl4ai                       # Up, pas de redémarrage en boucle
curl -fsS http://localhost:8051/health               # répond rapidement
docker compose logs --tail=100 mcp-crawl4ai          # pas de traceback au démarrage
```

### D2. Versions réellement installées dans l'image

```bash
docker compose exec mcp-crawl4ai python -c "from crawl4ai.__version__ import __version__ as v; import lxml, playwright; from importlib.metadata import version; print('crawl4ai', v, '| lxml', lxml.__version__, '| playwright', version('playwright'))"
docker compose exec mcp-crawl4ai crawl4ai-doctor
```

Attendu : `crawl4ai 0.9.4`, et `crawl4ai-doctor` sans erreur (il lance un crawl de test avec Chromium). Noter les versions de `lxml` et `playwright` pour mémoire.

### D3. Crawl réel avec le nouveau filtre, dans le conteneur

```bash
docker compose exec mcp-crawl4ai python - <<'EOF'
import asyncio, warnings
warnings.simplefilter("always")
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, DefaultMarkdownGenerator, PruningContentFilterLXML
HTML = "<html><body><article><h1>T</h1><p>" + "Real content sentence. " * 40 + "</p><table><tr><th>R</th><th>Q1</th><th>Q2</th></tr><tr><th rowspan='2'>EU</th><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table></article></body></html>"
async def main():
    cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilterLXML()))
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as c:
        r = await c.arun(url="raw:" + HTML, config=cfg)
        print("success:", r.success); print(r.markdown.raw_markdown); print("--- fit ---"); print(r.markdown.fit_markdown)
asyncio.run(main())
EOF
```

Attendu : `success: True`, aucune `DeprecationWarning`, et un `fit_markdown` non vide qui contient le paragraphe. Le tableau garde le même rendu qu'en 0.9.0 : la ligne `| 3 | 4 |` reste décalée, car l'expansion du rowspan ne concerne que `result.tables`.

### D4. Tests unitaires et d'intégration dans le conteneur

L'image n'embarque pas pytest, mais les tests de jobs sont exécutables directement et visent le Postgres du compose :

```bash
docker compose exec mcp-crawl4ai python tests/test_index_jobs.py
```

### D5. Outils MCP, depuis un client MCP (Claude Code ou autre)

| # | Outil et appel | Attendu |
|---|---|---|
| 1 | `get_markdown(url=<page article>, filter_mode="fit")` | Markdown nettoyé, non vide. Aucune ligne `DeprecationWarning` dans `docker compose logs mcp-crawl4ai`. |
| 2 | `get_markdown(..., filter_mode="raw")` puis `filter_mode="bm25", query="..."` | Fonctionnent comme avant. |
| 3 | `scrape_urls(url=<page>)` avec `USE_CONTEXTUAL_EMBEDDINGS=false` | Retour inline, `chunks_stored > 0`, sans `job_id`. |
| 4 | `scrape_urls(url=<page>)` avec `USE_CONTEXTUAL_EMBEDDINGS=true` | Retour immédiat avec `job_id` et `follow`. La commande `follow` montre le job passer à l'état terminal. |
| 5 | `smart_crawl_url(url=<sitemap.xml ou page>)` | Plusieurs pages crawlées via `arun_many`, pas d'erreur Playwright `TargetClosedError` dans les logs. |
| 6 | `smart_crawl_url(..., mode query)` | Indexation inline puis réponse RAG. |
| 7 | `perform_rag_query(query=..., source=<source crawlée en 3>)` | Résultats pertinents, rerank OK si `USE_RERANKING=true`. |
| 8 | `capture_screenshot(url=...)` | `screenshot_base64` non vide, décodable en PNG. |
| 9 | `generate_pdf(url=...)` | `pdf_base64` non vide, commence par `%PDF` une fois décodé. |
| 10 | `execute_js(url=..., scripts="return document.title;")` | Le titre de la page apparaît dans le résultat. |
| 11 | `generate_schema_html(url=...)` | HTML nettoyé, non vide. |
| 12 | `search(...)` / `searxng_search(...)` | Pas d'impact attendu, contrôle de non-régression. |

### D6. Mémoire

Pendant les tests 4 et 5, surveiller `docker stats mcp-crawl4ai`. La consommation doit rester sous le `mem_limit` (2560 MiB), sans OOM kill, comme en 0.9.0.

```bash
docker inspect mcp-crawl4ai --format '{{.State.OOMKilled}} restarts={{.RestartCount}}'
```

Attendu : `false restarts=0`.

## 5. Retour arrière

Revenir au commit précédent, ou remettre `crawl4ai==0.9.0` et `PruningContentFilter` à la main, puis :

```bash
docker compose build mcp-crawl4ai && docker compose up -d mcp-crawl4ai
```

Les données en base sont compatibles dans les deux sens : le schéma est inchangé.
