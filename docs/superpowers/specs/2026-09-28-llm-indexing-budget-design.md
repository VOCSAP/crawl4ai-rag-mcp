# Budget LLM de l'indexation, annulation serveur et banc de modèles CPU

**Date :** 2026-09-28
**Statut :** design validé par l'opérateur, implémentation non commencée
**Cartes roadmap :** `4102999a` (ce lot), `01976add` (annulation, absorbée par ce lot), `da7bb432` (durcissement `/jobs`, rouverte par la route `DELETE`), `a3ce8962` (progrès, déjà livré par les jobs)
**Mémoires Kleos :** #19548 (incident), #19549 (cause dans le code), #19550 (logs bufferisés), #19555 (décision), #19559 (résumés de code)

Ce document sert de feuille de route pour une session d'implémentation. Chaque étape porte une case à cocher : la cocher dans le même commit que le travail qu'elle décrit, avec le SHA en fin de ligne.

---

## 1. Objectif

**Critère non négociable : un seul appel d'outil ne peut plus occuper l'Ollama partagé plus de quelques minutes.** Concrètement, le temps LLM consommé par l'indexation d'un appel est borné par `CONTEXTUAL_BUDGET_SECONDS + LLM_TIMEOUT`, quel que soit le client MCP, la taille de la page ou le nombre de blocs de code.

Objectifs secondaires :

1. Un job lancé peut être arrêté sans redémarrer le conteneur.
2. Les logs de l'application sont lisibles en temps réel et survivent à un redémarrage.
3. Savoir, mesures à l'appui, si un petit modèle CPU dédié peut remplacer `qwen3:8b` pour cette tâche, et ainsi sortir crawl4ai de l'Ollama GPU de Kleos.

## 2. Pourquoi

### 2.1 L'incident du 2026-09-28

- 12:26:58 UTC : un `scrape_urls` sur `https://nodejs.org/api/fs.html`.
- De 12:27 à 14:14 : environ 100 appels `POST /v1/chat/completions` par heure depuis LXC 122, deux à la fois, de 60 à 80 s chacun.
- Ollama (LXC 116) n'a jamais été inactif. Les embeddings `bge-m3` de Kleos se sont empilés derrière, et ses `store` et `search` ont expiré.
- Un redémarrage manuel de `mcp-crawl4ai` à 14:24:16 UTC a tout débloqué.

### 2.2 Les causes dans le code

L'indexation d'un appel d'outil déclenche **trois** étapes LLM, toutes sur `MODEL_CHOICE`, toutes sans plafond :

| Étape | Lieu | Appels | Concurrence | `max_tokens` |
|---|---|---|---|---|
| Résumé de source | `extract_source_summary`, `src/utils.py:808` | 1 par source | 5 workers, `src/crawl4ai_mcp.py:911` | 150 |
| Enrichissement contextuel | `generate_contextual_embedding`, `src/utils.py:229` | 1 par chunk | `CONTEXTUAL_EMBEDDING_WORKERS` (défaut 2), `src/utils.py:361` | 200 |
| Résumé de bloc de code | `generate_code_example_summary`, `src/utils.py:606` | 1 par bloc de code | 10 workers, `src/crawl4ai_mcp.py:952` | 2000 |

La troisième étape ne tourne que si `USE_AGENTIC_RAG=true`, ce qui est le cas sur LXC 122 depuis au moins le 2026-09-02 (Kleos #15879). Une page de référence comme `fs.html` contient des centaines de blocs de code : cette étape peut coûter autant que l'enrichissement contextuel.

Aggravants :

- **Taille du prompt contextuel** : le document tronqué à `CONTEXTUAL_DOC_TRUNCATION` (8000 caractères), plus le chunk (5000), soit environ 3 500 à 4 000 tokens par appel (estimation).
- **Retries implicites** : `_get_openai_client` (`src/utils.py:23`) ne fixe pas `max_retries`, le SDK `openai` en fait donc 2. Un appel qui expire peut coûter jusqu'à 3 × `LLM_TIMEOUT`.
- **Rien n'arrête un job** : il tourne dans une `asyncio.create_task` rattachée au module (`src/crawl4ai_mcp.py:3205`), indépendante de la session MCP. Aucune route ne l'annule. Seul un redémarrage l'arrête, et le job passe alors `lost` sans reprise.
- **Logs inexploitables** : aucun `PYTHONUNBUFFERED`. Les `print()` sont retenus dans le tampon de stdout : la ligne `Use contextual embeddings: True` est horodatée 12:50:52 pour un scrape lancé à 12:26, et le contenu du tampon est perdu au redémarrage. Impossible de savoir aujourd'hui si des appels ont expiré.

### 2.3 Ce qui a été écarté

- **Désactiver `USE_CONTEXTUAL_EMBEDDINGS`** : garantie totale, mais perte de l'enrichissement partout pour un problème qui ne touche que les très grosses pages. Reste la solution de repli si le banc (phase 4) montre un gain de retrieval négligeable.
- **Un modèle GPU dédié plus petit** : chaque appel devient plus court, mais leur nombre reste illimité. Un deuxième modèle résident sur LXC 116 risque d'évincer `bge-m3`, ce qui est justement la panne de Kleos (décision du 2026-05-18, Kleos #1782).
- **Concurrence à 1** : libère un slot Ollama sur deux, mais la durée double. Ne borne rien. `CONTEXTUAL_EMBEDDING_WORKERS` reste à 2 par défaut, variable.
- **Un hook Claude Code qui détecte un timeout MCP et appelle l'annulation** : il ne verrait jamais le problème. `scrape_urls` répond en quelques secondes avec un `job_id` et l'appel MCP réussit. Le 28, aucun client n'a expiré. Un hook ne protégerait en plus que Claude Code, pas les autres clients MCP.

## 3. Design

### 3.1 Un budget LLM par appel d'outil

Un objet `LLMBudget` est créé une fois par indexation (un appel à `_index_crawl_payload`) et partagé par les trois étapes. Il porte :

- une échéance : `time.monotonic() + CONTEXTUAL_BUDGET_SECONDS` ;
- un compteur d'appels autorisés : `CONTEXTUAL_MAX_CHUNKS`, décrémenté de façon thread-safe (les appels partent de plusieurs pools de threads) ;
- un drapeau d'annulation (`threading.Event`) et la raison de l'arrêt : `budget_time`, `budget_calls`, `cancelled`, `client_gone`.

Chaque fonction qui appelle le LLM demande `budget.acquire()` **juste avant** l'appel, dans le thread worker. Si la réponse est non, elle rend sa valeur de repli existante, sans appeler le LLM :

| Étape | Repli déjà présent dans le code |
|---|---|
| Résumé de source | `f"Content from {source_id}"` |
| Enrichissement contextuel | le chunk brut, compté comme dégradé (`failed` du job) |
| Résumé de code | `"Code example for demonstration purposes."` |

Vérifier dans le thread worker, et non avant de soumettre au pool, règle le problème de granularité noté sur la carte `01976add` : le `ThreadPoolExecutor` soumet un batch de 20 chunks d'un coup, mais chaque future relit le budget au moment où il démarre.

**Arrêter les appels LLM n'arrête pas l'indexation.** `add_documents_to_db` supprime les lignes existantes de l'URL avant d'insérer (`src/utils.py:324-341`). Interrompre l'insertion laisserait l'URL sans aucune ligne en base. Tous les chunks sont donc toujours insérés : enrichis s'ils sont passés avant l'arrêt, bruts sinon. Les embeddings `bge-m3` restent calculés, un appel batché de moins d'une seconde par lot.

**Borne garantie** : au plus `CONTEXTUAL_BUDGET_SECONDS + LLM_TIMEOUT` de temps LLM par appel d'outil. Les appels en cours à l'échéance vont au bout ou expirent, sans retry.

**Limite assumée** : plusieurs appels d'outil successifs passent l'un après l'autre (`INDEX_JOB_CONCURRENCY=1`), chacun avec son budget. Le critère porte sur un appel, pas sur un débit global. Dans un appel multi-URL, les premières pages consomment le budget et les suivantes sont indexées brutes.

### 3.2 Annulation côté serveur

Le même drapeau sert trois déclencheurs, sans rien à appeler côté client :

1. **Le budget** (3.1) : automatique, couvre l'incident du 28.
2. **La déconnexion du client sur le chemin inline** : `smart_crawl_url` en mode `query` indexe dans l'appel (`allow_defer=False`, `src/crawl4ai_mcp.py:1415`). Si le client abandonne, `await asyncio.to_thread(...)` reçoit une `CancelledError`. On lève alors le drapeau (raison `client_gone`), puis on relance l'exception. Le thread termine vite, puisque tous les appels LLM restants sont refusés.
3. **`DELETE /jobs/{id}`** : lève le drapeau d'un job en attente ou en cours. Filet manuel, réponse `202` si le job existe et n'est pas terminé, `404` sinon, `409` s'il est déjà terminé.

Registre : `job_id -> LLMBudget` en mémoire du processus, rempli à la création du job (`_dispatch_indexing`) et vidé à la fin. Un seul processus sert le MCP, un registre en base serait inutile.

Traçabilité : colonne `stop_reason text` dans `index_jobs`, rendue par `GET /jobs/{id}` et le flux. `ensure_index_jobs_table` fait `CREATE TABLE IF NOT EXISTS`, qui n'ajoute rien à une table existante : il faut aussi un `ALTER TABLE index_jobs ADD COLUMN IF NOT EXISTS stop_reason text`. L'état terminal reste `done` : l'indexation a bien eu lieu, seul l'enrichissement a été écourté.

Sécurité de la route `DELETE` : la carte `da7bb432` prévoyait de rouvrir le durcissement dès qu'une route de mutation apparaîtrait sous `/jobs/`. Le proxy public rend 502 sur cet hôte et l'opérateur a décidé un accès LAN uniquement (carte `752dabd9`, classée `wont`). Le risque résiduel est un appel depuis le LAN, avec un `job_id` uuid non devinable. Consigner ce raisonnement sur `da7bb432` plutôt que de durcir dans ce lot.

### 3.3 Variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `CONTEXTUAL_BUDGET_SECONDS` | Durée maximale des appels LLM d'une indexation | `300` |
| `CONTEXTUAL_MAX_CHUNKS` | Nombre maximal d'appels LLM d'une indexation, toutes étapes confondues | `60` |
| `CONTEXTUAL_LLM_MAX_RETRIES` | `max_retries` du client `openai` pour ces trois étapes | `0` |
| `CONTEXTUAL_EMBEDDING_WORKERS` | Inchangé | `2` |

Défauts provisoires : à 31 s par appel (mesure du 2026-09-21) et 2 workers, 300 s enrichissent environ 19 chunks. À recalibrer avec les mesures de la phase 4. Documenter les trois nouvelles variables dans la table de `CLAUDE.md` (le dépôt n'a pas de `.env.example`).

### 3.4 Logs

`ENV PYTHONUNBUFFERED=1` dans le `Dockerfile`, ce qui couvre aussi les conteneurs jetables lancés depuis l'image (Kleos #18515). Au démarrage de l'indexation, une ligne de log récapitule budget, plafond, workers et modèle. À l'arrêt, une autre donne la raison, le nombre d'appels faits et le nombre de replis. Ces lignes passent par `print()`, comme le reste du serveur : aucun module n'utilise `logging`, et un `logging.basicConfig` au niveau INFO ferait remonter les journaux des bibliothèques tierces (httpx trace chaque requête). L'horodatage vient de Docker (`docker logs -t`), fiable dès que la sortie n'est plus bufferisée.

## 4. Plan d'exécution

### Phase 0 : préalables

- [x] Lire `KNOWN_ISSUES.md` et ajouter une section OPEN « indexation : charge LLM non bornée » qui renvoie à ce document.
- [x] Déclarer la spec agent-forge (`spec-task`) à partir des sections 1 et 3.
- [x] Passer la carte `01976add` en `in_progress` et y noter qu'elle est absorbée par ce lot.

### Phase 1 : budget LLM (TDD)

- [x] Test rouge : `LLMBudget` refuse après l'échéance, refuse après N acquisitions, reste correct sous accès concurrents (N threads, exactement `max` acquisitions acceptées).
- [x] Implémenter `LLMBudget` dans `src/utils.py`.
- [x] Test rouge qui **prouve la borne** : faux client LLM qui dort 0,5 s par appel, 50 chunks, 2 workers, budget de 2 s. Vérifier à la fois :
  - durée de l'indexation ≤ budget + 1 appel + marge ;
  - nombre d'appels LLM ≤ ce que permet le budget ;
  - les 50 chunks sont insérés ;
  - les chunks non enrichis sont comptés comme dégradés.
- [x] Brancher le budget dans les trois étapes (`extract_source_summary`, `generate_contextual_embedding`, `generate_code_example_summary`), vérification dans le thread worker.
- [x] Test : avec `USE_AGENTIC_RAG=true`, le plafond d'appels couvre aussi les résumés de code (compteur partagé, pas un plafond par étape).
- [x] `max_retries=CONTEXTUAL_LLM_MAX_RETRIES` sur le client de ces trois étapes. Test : un faux serveur qui expire n'est appelé qu'une fois.
- [x] `stop_reason` : migration `ADD COLUMN IF NOT EXISTS`, écriture en fin de job, exposition dans `GET /jobs/{id}` et le flux. Test sur une table créée sans la colonne.

### Phase 2 : annulation serveur (TDD)

- [x] Registre `job_id -> LLMBudget`, rempli dans `_dispatch_indexing`, vidé en fin de `_run_index_job`, y compris sur exception.
- [x] Test rouge puis implémentation de `DELETE /jobs/{id}` : `202` en cours ou en attente, `404` inconnu, `409` terminé. Un job en attente annulé indexe ses chunks bruts sans aucun appel LLM.
- [x] Chemin inline : `CancelledError` → drapeau `client_gone` → exception relancée. Test : annuler la tâche pendant l'indexation, puis vérifier qu'aucun nouvel appel LLM ne part et que les chunks sont insérés.
- [x] Consigner sur `da7bb432` le raisonnement de sécurité de la section 3.2.

### Phase 3 : logs, documentation, déploiement

- [x] `ENV PYTHONUNBUFFERED=1` dans le `Dockerfile`, lignes de log de début et de fin d'indexation.
- [x] Table des variables et section « Indexation asynchrone » de `CLAUDE.md`.
- [x] Suite complète via le sous-agent `test-runner`, puis `contract-check` contre ce document. 70/70 sur un Postgres jetable. 6cfcaa6
- [x] Commit, push, puis déploiement sur LXC 122 : 6cfcaa6
  `pct exec 122 -- bash -c "cd /opt/crawl4ai-rag-mcp && git pull && docker compose up -d --build mcp-crawl4ai"`
  (`--build` est obligatoire : le code vient de l'image, pas d'un volume.)
- [x] Validation réelle : relancer `scrape_urls` sur `https://nodejs.org/api/fs.html`. Relever dans les logs Ollama (MCP `docker-inspect`, `docker_logs ollama`, filtrés sur l'IP de LXC 122) l'heure du dernier appel venant de LXC 122, et vérifier qu'elle tombe avant `début + CONTEXTUAL_BUDGET_SECONDS + LLM_TIMEOUT`. Contrôler `stop_reason=budget_*` via `GET /jobs/{id}`.
  Résultat : `Indexing started` à 21:09:58 UTC, `Indexing finished` à 21:18:12 UTC avec `stop_reason=budget_time calls=9 degraded_fallbacks=117`, 121 chunks insérés dont 113 bruts. Le contrôle côté Ollama n'a pas pu être fait : le conteneur `ollama` n'a écrit aucune ligne de log sur toute la fenêtre du job, alors que 9 appels ont abouti. La borne n'est donc mesurée que côté application.
- [x] Section `KNOWN_ISSUES.md` passée en FIXED avec le SHA, `tests/TEST_RESULTS.md` mis à jour, résultat stocké dans Kleos (#19598).

### Phase 4 : banc de modèles CPU (sur ce PC)

But : trouver le plus petit modèle qui garde l'essentiel du **gain de retrieval** apporté par `qwen3:8b`, et mesurer son coût CPU.

- [ ] **Préalable opérateur** : relever le CPU de l'hôte Proxmox (`lscpu` : modèle, cœurs physiques, threads). D'après la mention d'un iGPU Radeon 760M, ce serait un Ryzen 5 à 6 cœurs : à confirmer, car 4 à 6 cœurs dédiés pèseraient lourd face à OPNsense.
- [ ] **Moteur** : `llama-server` (llama.cpp), qui expose l'API OpenAI et règle explicitement `--threads`, `--parallel` et `--cache-reuse`. C'est aussi le candidat de la phase 5, donc on mesure ce qu'on déploiera. Ollama en mode CPU reste une alternative si `llama-server` pose problème sous Windows.
- [ ] **Corpus** : une dizaine de pages variées (une très grosse doc de référence type `fs.html`, une doc Python, une page en français, un blog, la doc crawl4ai), récupérées en markdown brut (`get_markdown` ou `scrape_urls(return_raw_markdown=true)`) et découpées avec `smart_chunk_markdown` du projet. Échantillon d'environ 60 chunks. Scripts dans `bench/contextual/`, données dans `bench/contextual/data/` (gitignoré).
- [ ] **Questions** : une question par chunk, générée à partir du chunk **brut** (jamais du contexte, pour ne pas biaiser en faveur d'un modèle), par un modèle fort indépendant des candidats. Relecture rapide d'un échantillon.
- [ ] **Conditions comparées** : sans contexte, `qwen3:8b` (référence), puis les candidats. Liste de départ, à revérifier sur le web au démarrage de la phase : `qwen3:0.6b`, `qwen3:1.7b`, `qwen3:4b`, `gemma3:1b`, `llama3.2:1b`, `lfm2` 1,2B. Même prompt que `generate_contextual_embedding`, plus une variante avec `CONTEXTUAL_DOC_TRUNCATION=4000`.
- [ ] **Mesures qualité** : pour chaque condition, embeddings `bge-m3` de « contexte + chunk », puis recall@5 et MRR des questions sur le pool complet des chunks. Contrôle de format : longueur, absence de préambule, langue du chunk respectée.
- [ ] **Mesures coût** : secondes par chunk (médiane et p95) à 2, 4 et 6 threads, avec et sans cache de préfixe ; RAM résidente. Le cache de préfixe est le facteur décisif : le document de 8000 caractères est identique d'un chunk à l'autre de la même page, seul le chunk devrait être recalculé.
- [ ] **Règle de décision**, à valider avec l'opérateur avant de lancer la mesure :
  - gain relatif = (MRR modèle − MRR sans contexte) / (MRR `qwen3:8b` − MRR sans contexte) ;
  - retenir le plus petit modèle avec un gain relatif ≥ 0,8 et un p95 compatible avec le budget ;
  - si le gain de `qwen3:8b` lui-même est inférieur à environ 0,02 de MRR absolu, l'enrichissement ne vaut pas son coût : recommander `USE_CONTEXTUAL_EMBEDDINGS=false`.
- [ ] **Limite à garder en tête** : la qualité mesurée sur ce PC se transpose telle quelle, pas la latence. Refaire une courte mesure de latence sur l'hôte cible, dans un conteneur limité en cœurs.
- [ ] Rapport dans `docs/bench/2026-xx-contextual-cpu-models.md` (tableau des conditions, recommandation), décision stockée dans Kleos.

### Phase 5 : déploiement d'un modèle CPU (conditionnelle)

Seulement si la phase 4 retient un modèle.

- [ ] Deux variables de plus : `CONTEXTUAL_LLM_BASE_URL` (repli sur `OLLAMA_BASE_URL`) et `CONTEXTUAL_MODEL` (repli sur `MODEL_CHOICE`), lues par les trois étapes de 3.1. Le rerank et `extract_structured` restent sur leurs variables actuelles.
- [ ] Service `llama-server` dans `docker-compose.yml` avec `cpus:` et `mem_limit:` explicites. Vérifier la RAM disponible de LXC 122 avant, puisque `mcp-crawl4ai` est déjà limité à 2 GiB.
- [ ] Validation réelle comme en phase 3, en vérifiant en plus **zéro** appel venant de LXC 122 dans les logs Ollama de LXC 116 pendant le scrape.
- [ ] Recalibrer `CONTEXTUAL_BUDGET_SECONDS` et `CONTEXTUAL_MAX_CHUNKS` d'après les mesures de latence sur l'hôte.

## 5. Critères d'acceptation

1. Un test automatisé prouve la borne : durée LLM ≤ budget + 1 appel, tous les chunks insérés.
2. Le budget couvre les trois étapes LLM, résumés de code compris.
3. Aucun retry implicite sur ces appels.
4. `DELETE /jobs/{id}` arrête les appels LLM d'un job en attente ou en cours ; la déconnexion d'un client sur le chemin inline fait de même.
5. `stop_reason` est visible dans `GET /jobs/{id}`.
6. Sur LXC 122, un scrape de `fs.html` ne produit plus d'appel LLM au-delà de `CONTEXTUAL_BUDGET_SECONDS + LLM_TIMEOUT`, mesuré dans les logs Ollama.
7. Les logs de l'application sont horodatés en temps réel.
8. Le banc produit une recommandation chiffrée : un modèle CPU, `qwen3:8b` sur GPU, ou la désactivation.
