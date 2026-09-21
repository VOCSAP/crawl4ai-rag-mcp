# Indexation asynchrone par jobs

**Date :** 2026-09-21
**Cartes roadmap :** `a3ce8962` (progres / timeout), `01976add` (annulation, hors lot), `da7bb432` (durcissement /jobs, hors lot)
**Statut :** design valide par l'operateur, implementation non commencee

## 1. Le probleme

Mesure du 2026-09-21 sur `scrape_urls` avec 8 pages :

| Phase | Duree |
|---|---|
| Fetch des 8 pages | 15 s |
| 37 appels LLM de contextual embedding vers Ollama | environ 31 s chacun |
| Total | environ 19 min |

Pendant ces 19 minutes, aucune notification MCP n'est emise. Le client coupe a 300 s de silence.

### La cause n'est pas celle qu'on croyait

L'exploration a montre que le pipeline **bloque l'event loop du serveur**. `_process_multiple_urls` est
une coroutine, mais elle appelle `add_documents_to_db(...)` en direct, sans `await` ni executor
(`src/crawl4ai_mcp.py:1073` et `:1351`). Tout en dessous est synchrone : `generate_contextual_embedding`
fait un `client.chat.completions.create` bloquant (`src/utils.py:246`), orchestre par un
`ThreadPoolExecutor` dont le bloc `with` attend la fin de tous les futures (`src/utils.py:348`).

La discipline existe pourtant dans le repo : `_rerank_local` deporte son travail synchrone via
`run_in_executor` (`src/crawl4ai_mcp.py:312`). Le chemin d'indexation ne l'a jamais recue.

Trois consequences, qui recadrent le probleme :

1. Aucune notification de progres n'est **physiquement emettable** : il n'y a aucun tour d'event loop
   pour la poster. Ecrire le code de progres sans corriger cela ne produirait rien.
2. Aucune annulation ne peut etre livree : une coroutine bloquee dans du code sync ne repasse jamais
   par un point d'`await` ou `CancelledError` s'injecte.
3. Le serveur ne repond a **rien d'autre** pendant ce temps, `/health` et les autres sessions MCP
   comprises. C'est vraisemblablement ce qui a ete observe lors de l'incident du 2026-09-21 :
   « le port 8051 acceptait le TCP mais ne repondait plus en HTTP ».

Le point 3 est un defaut de disponibilite independant du confort d'usage. Il justifie a lui seul de
sortir ce pipeline de l'event loop, quelle que soit la suite.

## 2. La decision et ce qui a ete ecarte

**L'indexation n'a pas besoin d'etre synchrone avec le tool call.** Avec `return_raw_markdown=true`,
l'appelant veut le markdown, disponible a 15 s. Sinon il veut savoir que l'indexation est partie.
Dans les deux cas, attendre la fin de l'indexation ne lui apporte rien.

**Retenu :** `scrape_urls` rend le fetch immediatement et un `job_id`. L'indexation devient un travail
de fond suivi par un flux HTTP.

**Ecarte -- keep-alive par notifications de progres.** Garder la semantique actuelle et emettre un
`notifications/progress` pour tenir le client eveille. Deux defauts : l'agent reste immobilise pendant
toute la duree sans rien pouvoir faire, et l'option repose sur une hypothese non mesuree, a savoir que
Claude Code remette a zero son timer de 300 s en recevant un progres. Ne resout pas le vrai besoin.

**Ecarte -- rester sous les 300 s par reglage.** Monter `CONTEXTUAL_EMBEDDING_WORKERS` ferait passer
les 37 chunks sous le seuil. Le mur revient sur un site plus gros. Le reglage reste utile mais ne
tient pas lieu d'architecture.

**Ecarte -- un CLI dedie.** L'idee initiale prevoyait un binaire cote client. Le flux HTTP le rend
inutile : `curl` est deja present partout, et `Bash(run_in_background=true)` fournit nativement le
« l'agent attend sagement et est re-invoque a la fin ». Rien a livrer ni a maintenir sur les postes.

**Ecarte -- un long-poll silencieux.** `proxy_read_timeout` de nginx, donc de NPM, est un delai entre
deux lectures et non une duree totale. Un `curl` qui attend sans recevoir d'octet est coupe a 60 s.
Un flux qui emet regulierement ne l'est jamais, quelle que soit sa duree.

## 3. Architecture

```
scrape_urls(urls)
    |
    |-- fetch (15 s)  ------------------------------> markdown
    |-- ecrit les chunks + une ligne dans index_jobs
    |-- repond: {markdown, job_id, indexing: "queued", follow: "curl -sN .../jobs/<id>/stream"}
    |
    v
  (le tool call est termine, l'agent est libre)

worker singleton process-wide
    |-- prend le prochain job queued (jusqu'a INDEX_JOB_CONCURRENCY actifs)
    |-- pour chaque chunk: appel LLM, puis heartbeat_at + done += 1
    |-- etat final: done / failed / error
    |
    v
GET /jobs/<id>/stream   -->  une ligne par chunk, keep-alive toutes les 10 s,
                             fermeture du flux a la terminaison du job
```

### Pourquoi l'etat vit en Postgres

Un dictionnaire en memoire disparait a l'OOM kill et au `docker compose up -d --build` du deploiement.
Le suiveur attendrait alors un resultat qui n'arrivera jamais. Le serveur a deja une base et deja sa
couche d'acces unique dans `src/utils.py`.

Le benefice ne se limite pas a la persistance : il rend la **detection des jobs perdus gratuite**. Le
worker touche `heartbeat_at` a chaque chunk. Un job `running` dont le heartbeat depasse
`INDEX_JOB_STALE_SECONDS` est mort avec son process ; le flux le declare perdu au lieu de faire
attendre indefiniment. Aucun mecanisme dedie n'est necessaire.

### Pourquoi le worker est process-wide

Une tache rattachee a une session MCP meurt avec elle, ou se duplique par session : `crawl4ai_lifespan`
est re-entre une fois par session MCP. C'est exactement le defaut qui a produit la fuite Chromium
(carte `3d1abdd9`). Le worker est un singleton module-level, decouple de la requete qui a cree le job,
donc insensible a l'annulation anyio du tool call.

## 4. Composants

### 4.1 Table `index_jobs`

| Colonne | Role |
|---|---|
| `id` | uuid, non devinable, rendu a l'appelant |
| `state` | `queued`, `running`, `done`, `failed`, `lost` |
| `total`, `done`, `failed` | compteurs de chunks |
| `error` | message d'erreur si `state = failed` |
| `created_at`, `started_at`, `finished_at` | horodatage |
| `heartbeat_at` | touche a chaque chunk, base de la detection des jobs perdus |

`lost` n'est pas ecrit par le worker mais derive a la lecture : un `running` dont le heartbeat est
perime est rendu comme `lost`. Un job mort par OOM ne peut pas, par definition, ecrire son propre etat
final.

Le schema va dans `crawled_pages.sql`. Attention : ce fichier n'est execute qu'au premier `up` via
`/docker-entrypoint-initdb.d/`. La table doit donc etre creee par une migration idempotente au demarrage
du serveur, sinon un deploiement sur une base existante ne la verra jamais.

### 4.2 Worker singleton

Meme forme que `_get_shared_crawler` : un objet module-level, demarre a la premiere utilisation,
detache de toute session. Il consomme les jobs `queued` et maintient au plus `INDEX_JOB_CONCURRENCY`
jobs actifs.

Le travail lui-meme reste synchrone et tourne hors de l'event loop via `asyncio.to_thread`. C'est ce
qui corrige le defaut de disponibilite du point 1.3.

### 4.3 Routes HTTP

Enregistrees en `@mcp.custom_route`, comme `/health`, donc servies a la fois par l'app Streamable HTTP
et par l'app SSE.

- `GET /jobs/{id}` : etat ponctuel en JSON.
- `GET /jobs/{id}/stream` : une ligne par chunk indexe, un keep-alive toutes les 10 s quand rien ne
  progresse, fermeture du flux a la terminaison. Les 10 s sont choisis tres en dessous des 60 s de
  nginx pour qu'aucun reglage de NPM ne soit necessaire.

Un `id` inconnu rend 404 et non un flux vide, sinon un suiveur lance sur un id errone attendrait pour
toujours.

### 4.4 Changement de `scrape_urls`

Le retour gagne `job_id`, `indexing` et `follow`. Le champ `follow` porte la commande `curl` prete a
l'emploi, pour que l'appelant n'ait ni URL ni forme a deviner.

**Quand il n'y a pas de job :** si `USE_CONTEXTUAL_EMBEDDINGS` est a `false`, le pipeline tient en
quelques secondes. Aucun job n'est cree, les trois champs sont absents, et la semantique actuelle est
integralement preservee. La complexite n'est payee que lorsqu'elle sert.

## 5. Variables d'environnement

| Variable | Role | Defaut |
|---|---|---|
| `INDEX_JOB_CONCURRENCY` | Jobs d'indexation traites en parallele | `1` |
| `INDEX_JOB_STALE_SECONDS` | Age du heartbeat au-dela duquel un job `running` est rendu `lost` | `300` |

Rien n'est code en dur, conformement a la regle du projet.

**La charge reelle est un produit, pas une somme.** `CONTEXTUAL_EMBEDDING_WORKERS` (defaut 2) est le
nombre d'appels LLM concurrents a l'interieur d'un job. Le nombre de requetes simultanees vers Ollama
vaut `INDEX_JOB_CONCURRENCY x CONTEXTUAL_EMBEDDING_WORKERS`. A 4 jobs et 8 workers, cela fait 32
requetes concurrentes, et le goulot se deplace de la RAM du container vers le GPU et vers
`OLLAMA_NUM_PARALLEL`. Les deux variables se reglent ensemble.

**Augmenter la RAM de l'hote ne suffit pas.** Le container est cappe par `docker-compose.yml` a
`mem_limit: 2560m` et `memswap_limit: 3g`, soit environ 512 Mo de swap effectif, contrairement aux trois
autres services de la stack ou `memswap_limit` egale `mem_limit`. Monter `INDEX_JOB_CONCURRENCY` sans
monter `mem_limit` reproduit l'incident du 2026-09-21. Et comme le
`MemoryAdaptiveDispatcher` de crawl4ai lit `/proc/meminfo` et non le cgroup (mesure en section VII de
`KNOWN_ISSUES.md`), il verra la RAM de l'hote et ne freinera jamais.

Ces valeurs sont lues a l'import : les ajuster demande `docker compose up -d`, jamais `restart`.

## 6. Erreurs et cas limites

| Cas | Comportement attendu |
|---|---|
| Le suiveur n'est jamais lance | Le job s'indexe quand meme. Le suivi est facultatif, pas structurel. |
| Le process meurt en cours de job (OOM, redeploiement) | Le heartbeat se perime, le job est rendu `lost`, le flux se ferme sur cet etat. |
| Deux `scrape_urls` coup sur coup a concurrence 1 | Le second reste `queued`, le flux le dit. Aujourd'hui les deux se serialisent deja, mais par accident. |
| Un chunk echoue | `failed` est incremente, le job continue. Le pipeline actuel se rabat deja sur le chunk brut en cas d'erreur LLM. |
| `job_id` inconnu | 404, pas un flux vide. |
| Le client coupe le flux | Sans effet sur le job, qui poursuit. |

## 7. Tests

1. Un `scrape_urls` avec contextual embeddings actif rend la main en quelques secondes et porte un
   `job_id`, alors que l'indexation n'est pas terminee.
2. Avec `USE_CONTEXTUAL_EMBEDDINGS=false`, le retour ne porte aucun des trois nouveaux champs.
3. Le serveur reste repondant pendant l'indexation : un `GET /health` aboutit alors qu'un job tourne.
   C'est le test qui mord sur le defaut de disponibilite du point 1.3, et il echouerait aujourd'hui.
4. Un job dont le `heartbeat_at` est force dans le passe est rendu `lost`.
5. A `INDEX_JOB_CONCURRENCY=1`, deux jobs soumis ensemble donnent un `running` et un `queued`.
6. `GET /jobs/<id inconnu>` rend 404.

Le test 3 est le plus important : c'est le seul qui distingue « le pipeline a ete deplace » de
« le pipeline a ete renomme ».

## 8. Hors perimetre de ce lot

- **Annulation d'un job** (carte `01976add`). Le design la rend presque triviale, un flag relu par le
  worker entre deux chunks, mais elle n'est pas traitee ici. Decision operateur : YAGNI.
- **Durcissement des routes `/jobs`** (carte `da7bb432`). Elles sont publiques via NPM, comme `/health`.
  Risque assume : `job_id` non devinable, contenu limite a des compteurs. A rouvrir si une route de
  mutation est ajoutee sous `/jobs/`, auquel cas l'absence d'authentification cesse d'etre benigne.
- **Reglage de `CONTEXTUAL_EMBEDDING_WORKERS`** et la question de savoir pourquoi un appel contextual
  prend 31 s. Utile, independant, mesurable separement.
