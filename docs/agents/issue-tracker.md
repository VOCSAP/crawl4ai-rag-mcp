# Issue tracker: roadmap partagée claude-peers

Les issues, specs et tickets de ce repo vivent dans la **roadmap partagée
claude-peers** (backlog persistant scopé au dépôt, partagé entre toutes les
sessions Claude). Toutes les opérations passent par les outils MCP
`mcp__claude-peers__roadmap_*`. Aucune CLI, aucun fichier ticket dans le repo.

Les GitHub Issues sont désactivées sur `VOCSAP/crawl4ai-rag-mcp` (fork de
`ToKiDoO/crawl4ai-rag-mcp`) : ne jamais tenter `gh issue`.

## Conventions

- **Créer**  : `roadmap_add` -- `title` obligatoire ; toujours remplir `context`
  (briefing pour une session future sans aucun contexte : objectif, périmètre,
  fichiers et tests concernés, critères d'acceptation, décisions prises).
  `kind` : feature | bug | debt | idea | chore. `priority` MoSCoW :
  must | should | could | wont.
- **Lire**   : `roadmap_get` avec l'id complet ou un préfixe unique (les 8
  caractères affichés par `roadmap_list`, ex. `752dabd9`).
- **Lister** : `roadmap_list` avec un filtre -- jamais sans. Filtres :
  `statuses`, `kinds`, `priorities`, `triages`, `tags`, `q` (titre/description/
  tags), `q_deep` (étend à rationale + context), `order: "queue"`.
- **Commenter** : `roadmap_append_context` -- ajoute sans remplacer, et passe
  outre le verrou de travail d'un autre agent. `roadmap_update.context`
  REMPLACE le champ entier.
- **Étiqueter** : `roadmap_update` avec `triage` (rôles de triage) ou `tags`
  (vocabulaire libre, aucune création préalable nécessaire).
- **Clore**  : `roadmap_update` `status: "done"`. `roadmap_archive` masque la
  carte des listes par défaut (soft delete réversible).

## Verrou de travail

`status: "in_progress"` VERROUILLE la carte sous ton `peer_id`. Ne le poser
qu'au moment où le travail démarre réellement, et le repasser à `planned` si tu
t'arrêtes avant d'avoir fini. Une écriture de statut sur une carte verrouillée
par un autre peer est refusée (409).

`triage: "wontfix"` exige `priority: "wont"`.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Fork privé, pas de contributions externes.)_

## Relation avec KNOWN_ISSUES.md

`KNOWN_ISSUES.md` reste le journal technique des bugs serveur (symptôme
reproductible, cause racine, fix + SHA), tenu selon le workflow de
`CLAUDE.local.md`. La roadmap porte le *backlog* : ce qui est à faire et son
état. Un bug ouvert vit dans les deux, la carte renvoyant à la section.

## Quand un skill dit « publish to the issue tracker »

Appeler `roadmap_add`.

## Quand un skill dit « fetch the relevant ticket »

Appeler `roadmap_get` avec l'id ou son préfixe.

## Wayfinding operations

Utilisé par `/wayfinder`. La **carte** est un item de roadmap ; les **tickets
enfants** sont d'autres items reliés par `depends_on`.

- **Map** : `roadmap_add` avec `tags: ["wayfinder:map"]`, corps Notes /
  Decisions-so-far / Fog dans `description`.
- **Ticket enfant** : `roadmap_add` avec `tags: ["wayfinder:<type>"]`
  (`research` / `prototype` / `grilling` / `task`) et
  `depends_on: ["<id de la map>"]`.
- **Blocage** : `depends_on`. Un ticket est débloqué quand chaque bloqueur est
  en `done` ou archivé.
- **Frontier query** : `roadmap_list` `statuses: ["planned"]`,
  `order: "queue"` ; écarter toute carte dont un `depends_on` n'est pas `done`,
  et toute carte déjà `in_progress` ; la première en ordre de queue gagne.
- **Claim** : `roadmap_update` `status: "in_progress"` -- première écriture de
  la session, elle pose le verrou.
- **Resolve** : `roadmap_append_context` avec la réponse, puis
  `roadmap_update` `status: "done"`, puis `roadmap_append_context` sur la map
  pour étendre Decisions-so-far.
