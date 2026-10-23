# PrintWatch : revue de code complète

> **Document historique.** Il décrit une version antérieure à la 0.13.0 ; l'état actuel est décrit dans le `README.md` (§20 Historique) et le `CHANGELOG.md`.

Livraison analysée : **V11 (0.11.0)** → corrections livrées en **V12 (0.12.0)**.
Date de la revue : 10 octobre 2026.

---

## 1. Périmètre et méthode

| Zone | Volume | Ce qui a été fait |
|---|---|---|
| Paquet Python `printwatch/` (16 modules) | 2 555 lignes | **Lecture ligne à ligne** de tous les modules |
| Tests `tests/` | 1 758 lignes, 130 tests → **152** | Lecture ; mesure de couverture ; ajout de tests de non-régression |
| Interface `frontend/src/` | 1 158 lignes (TypeScript, CSS) | Relecture ciblée de la logique (états, appels API, formulaires, gestion du jeton) ; recherche de motifs à risque ; `npm audit` ; TypeScript strict |
| Outils `tools/`, `deploy/`, `Dockerfile`, `docker-compose.yml`, `scripts/powershell/` | 311 lignes d'outils + fichiers de déploiement | Lecture de `diagnostic.py`, `seed_demo.py`, `demo.ps1`, systemd et Docker ; **balayage par motifs seulement** pour les scripts PowerShell d'audit (secrets, politique d'exécution) |

Outils automatiques utilisés : `pytest-cov` (couverture), `bandit` (sécurité), `vulture` (code mort), `radon` (complexité), `mypy` (typage), `ruff` (lint), `npm audit`.
En complément : **mesures de performance** sur une base réaliste (21 imprimantes, 35 jours d'historique) et **tests bout en bout** d'un serveur réel (collecte en arrière-plan, requêtes HTTP).

**Ce que cette revue ne peut pas établir** (voir §10) : le comportement sur de vrais équipements autres que ceux déjà observés, l'exécution de Docker, de systemd et du lanceur Windows, le rendu visuel de l'interface dans un navigateur.

---

## 2. Verdict

Le projet est **sain et bien structuré** : séparation nette entre collecte, normalisation, alertes, stockage et présentation ; logique métier testée ; secrets correctement traités ; défauts du dernier kilomètre plutôt que de conception.

La revue a toutefois trouvé **9 défauts réels**, dont **un problème de performance qui se serait aggravé avec le temps**, deux faiblesses de sécurité et plusieurs risques de données erronées. **Tous sont corrigés en V12**, avec un test de non-régression chacun. Il reste 8 points ouverts, sans urgence (§6).

| | Avant (V11) | Après (V12) |
|---|---|---|
| Tests | 130 | **152** |
| Couverture | 82 % | **93 %** |
| Couverture de `cli.py` | 36 % | **78 %** |
| Couverture de `notify.py` | chemin d'envoi SMTP **non testé** | **100 %** |
| `GET /api/status` avec 35 jours d'historique, relevés toutes les 30 min | 299 ms | **3 ms** |
| idem, relevés toutes les 10 min (ton réglage) | **1 002 ms** | **3 ms** |
| Vulnérabilités `npm audit` (production) | 0 | 0 |

---

## 3. Constats corrigés en V12

Gravité : **Élevée** (se dégrade ou nuit gravement), **Moyenne** (données erronées ou faiblesse de sécurité exploitable sous conditions), **Faible** (robustesse, hygiène).

| # | Gravité | Domaine | Constat | Correction |
|---|---|---|---|---|
| R1 | **Élevée** | Performance | `GET /api/status` **recalculait la prévision de chaque consommable à chaque requête** : une requête SQL par consommable puis une régression sur tout l'historique. Coût **proportionnel à la durée d'historique** : négligeable aujourd'hui, mais 300 ms à 35 jours (cycle de 30 min) et **1 s à 35 jours avec un cycle de 10 minutes**, soit le réglage utilisé. Le tableau de bord interroge cette route toutes les 60 s, et toutes les 1,5 s pendant une synchronisation : le serveur aurait fini par saturer. | La prévision est **calculée une fois par cycle par le collecteur** et mémorisée (`fc_days`, `fc_conf`). L'API ne fait que la lire. Migration automatique des bases existantes ; une base ancienne ouverte en lecture seule ne plante pas. Test : l'API ne doit plus appeler `history`. |
| R2 | Moyenne | Fiabilité des données | Un consommable **disparu de la table SNMP** (changement de firmware, réindexation) restait en base avec son ancienne valeur, **jamais marqué périmé**, et pouvait **entretenir une alerte critique indéfiniment**. | L'API marque comme **périmé** tout consommable que le dernier relevé réussi n'a pas rafraîchi, et l'exclut du calcul d'état de l'imprimante. Test avec un « fantôme » critique. |
| R3 | Moyenne | Sécurité | **Rebinding DNS** : avec la gestion « ouverte » en écoute locale, une page web malveillante dont le nom DNS pointerait vers `127.0.0.1` envoyait des requêtes dont `Origin` et `Host` étaient cohérents, donc **acceptées par le contrôle anti-CSRF**, et pouvait appeler les routes d'écriture. | Garde sur l'en-tête **`Host`** : en écoute locale, seuls `localhost`, `127.0.0.1` et `[::1]` sont acceptés (sinon `400`). Variable `PRINTWATCH_ALLOWED_HOSTS` pour un proxy inverse. Vérifié sur un serveur réel. |
| R4 | Moyenne | Fonctionnel | Ajouter une imprimante (ou changer son adresse) **pendant un cycle en cours** : la demande de synchronisation était **perdue** (« déjà en cours »), alors que le cycle en cours ne contenait pas la nouvelle imprimante. L'interface annonçait « premier relevé en cours » mais il fallait attendre jusqu'à 30 minutes. | `request(queue=True)` : un second cycle est **mis en file** et démarre dès la fin du premier. Le simple clic sur « Synchroniser » garde son comportement (et sa limite anti-martèlement). |
| R5 | Moyenne | Fiabilité des données | CSV : `enabled` ne reconnaissait pas **`VRAI`** (booléen d'Excel en français) et **désactivait silencieusement** toute valeur inconnue ; les adresses en double passaient. | `vrai`/`faux`/`oui`/`non`/`1`/`0` reconnus ; toute autre valeur **refusée** avec un message précis ; adresse en double refusée. |
| R6 | Moyenne | Fiabilité des alertes | SMTP : le port **465** (TLS implicite) n'était pas géré ; un mot de passe absent de l'environnement faisait **tenter l'authentification avec un mot de passe vide** ; le chemin d'envoi n'était **jamais testé**. | `SMTP_SSL` sur le port 465 ; **erreur explicite** si la variable manque (les événements restent en attente, rien n'est perdu) ; tests des trois modes de transport et du mot de passe. |
| R7 | Faible | Performance | La purge de l'historique parcourait toute la table `samples` **à chaque cycle**, en tenant le verrou d'écriture (qui bloque aussi les modifications faites depuis le dashboard). | Au plus **une fois par heure**. |
| R8 | Faible | Sécurité | Lecture HP (LEDM) : une imprimante compromise pouvait **rediriger la requête vers un autre hôte** (requête interne « aveugle ») ou **faire goutter la réponse** pour bloquer un thread de collecte. | Redirections vers un autre hôte refusées ; **durée totale** bornée. *Le test a d'ailleurs révélé un défaut de ma première version* : `read(n)` attend les n octets, ce qui neutralisait la limite ; remplacé par `read1`. |
| R9 | Faible | Cohérence | `matrix` lisait le CSV et non la base, alors que la base fait foi depuis la V9. | Sans `-p`, utilise la liste de la base. |

Corrections d'hygiène : MD5 de la démonstration déclaré non cryptographique (`bandit` le signalait en « haute »), commentaire obsolète (Recharts), route d'historique qui utilisait l'heure système au lieu de l'horloge injectée.

---

## 4. Appréciation de l'architecture

### Ce qui est solide

1. **Un seul écrivain pour les relevés.** Seul le collecteur écrit les relevés, et les threads d'interrogation ne touchent jamais la base ; les routes de gestion n'écrivent que la liste d'imprimantes, en transactions courtes (SQLite en mode WAL). Pas de verrous applicatifs, pas de course sur les relevés.
2. **Fonctions pures testables.** `alerts`, `forecast`, `normalize`, `series` n'ont aucun effet de bord ; l'horloge est injectée (`clock`, `sleep`) : les tests de collecte sont déterministes et instantanés.
3. **Secrets bien traités.** Fichier `snmp.conf` temporaire en 0600 dans un dossier 0700, jamais en ligne de commande ; la validation refuse guillemets et retours à la ligne dans les secrets ; hôtes validés (une adresse commençant par `-` serait lue comme option) ; `matrix` sans adresse IP.
4. **Dégradation maîtrisée.** Chaque source optionnelle (HP, SMTP, prévision) échoue sans bloquer la collecte ; la boucle ne meurt pas sur une erreur ; les événements non notifiés sont conservés.
5. **Machine à états d'alerte soignée.** Hystérésis correcte (vérifiée à la main sur les transitions critique → attention → OK), événements sans doublon, détection de remplacement.
6. **Honnêteté des données.** Valeurs inconnues (`-1`, `-2`, `-3`) jamais transformées en pourcentage ; imprimantes injoignables marquées périmées ; bandeaux d'état de la collecte.
7. **Migrations additives automatiques**, y compris pour une base ouverte en lecture seule.

### Points d'attention structurels

- **`cli.py` concentre trop** (commandes, démo, import/export, câblage du serveur) : 306 instructions, plusieurs fonctions complexes (`cmd_matrix` 23, `cmd_serve` 19, `cmd_status` 15). Pas un défaut aujourd'hui, mais le prochain ajout devrait s'accompagner d'un découpage (`commands/`).
- **`load_printers`** (complexité 27) mélange décodage, détection du séparateur, validation et construction : à scinder si le format évolue.
- **Trois routes d'API (`/api/series`, `/api/availability`, `/api/fleet/daily`) et `series.py` (≈ 140 lignes)** ne servent plus à l'interface. Elles sont testées et documentées pour d'éventuelles intégrations ; à conserver **ou** supprimer, mais pas à laisser vieillir.
- **Pas de numéro de version de schéma** (`PRAGMA user_version`) : les migrations actuelles (ajout de colonnes) sont sûres, mais un futur changement non additif demandera un mécanisme.

---

## 5. Revue module par module

| Module | Rôle | Appréciation et remarques |
|---|---|---|
| `snmp.py` | Appels `snmpwalk` / `snmpget`, analyse de la sortie | **Bon.** Gestion des chaînes hexadécimales (accents), des lignes multiples, des « no value ». Un `snmp.conf` laissé en cas d'arrêt brutal (`SIGKILL`) subsiste dans `/tmp` (0600, dossier 0700) : risque faible. |
| `normalize.py` | Table brute → consommables | **Bon.** Réceptacles inversés, couleurs devinées par motif (français et anglais), valeurs spéciales traitées. Complexité 14 acceptable. |
| `alerts.py` | Machine à états | **Très bon.** Court, pur, vérifié sur les transitions limites. |
| `forecast.py` | Rupture estimée | **Correct.** Heuristique de confiance simple ; la « chute » est calculée sur les extrêmes bruts (sensible à une valeur isolée). Coût déplacé dans le collecteur (R1). |
| `collector.py` | Cycle de collecte | **Bon** (97 % de couverture). Reprise après interruption, relance, complément HP. Risque connu : si l'adresse d'une imprimante est modifiée **pendant** un cycle, les relevés de l'ancienne adresse sont rattachés à la fiche (au plus un cycle de données erronées, voir O4). |
| `storage.py` | SQLite | **Bon** (98 %). Requêtes paramétrées ; les trois f-strings SQL signalées par `bandit` portent sur des **constantes ou des listes blanches** (faux positifs). Pas d'index unique sur l'adresse d'une imprimante active (O2). |
| `ledm.py` | Complément HP | **Bon après R8.** XML d'un équipement réseau traité avec prudence (taille, `DOCTYPE`/`ENTITY` refusés, durée et redirections bornées). |
| `notify.py` | Courriel / journal | **Correct après R6.** Corps en texte brut ; les noms saisis dans le dashboard ne peuvent pas injecter d'en-têtes (caractères de contrôle refusés). Heure locale sans fuseau dans le message (cosmétique). |
| `config.py` | TOML, secrets, CSV | **Bon après R5.** Validation stricte avec messages précis ; complexité de `load_printers` à surveiller. |
| `model.py` | Types | Propre. |
| `web.py` | API, en-têtes de sécurité, gestion | **Bon** (98 %). CSP stricte sans CDN ; routes d'écriture gardées (gestion active, origine, jeton en temps constant, champs bornés). Faiblesse corrigée : R3. `printer_state` renvoie « OK » pour une imprimante dont aucun niveau n'est connu (seul le front la range en « sans données ») : voir O3. |
| `sync.py` | Pont tableau de bord → collecte | **Bon** (100 %) après R4. |
| `series.py` | Séries, disponibilité, synthèse quotidienne | **Correct**, fonctions pures bien documentées (limites de précision explicitées). Plus utilisé par l'interface (voir §4). |
| `demo.py` | Mode démonstration | **Bon.** Isolé : n'écrit que `demo.db`, ignore la configuration (donc ni SMTP réel ni base de production). |
| `cli.py` | Commandes | **Correct**, mais volumineux (voir §4) ; couverture portée de 36 % à 78 %. |
| `frontend/` | Interface | État dérivé du serveur, aucun `dangerouslySetInnerHTML`, `eval` ni `innerHTML` ; types stricts ; focus et ARIA gérés ; jeton administrateur en `sessionStorage` (oublié à la fermeture de l'onglet). **Pas de tests automatisés côté interface.** |
| `tools/`, `deploy/` | Outils, déploiement | `diagnostic.py` en lecture seule (conforme à sa promesse) ; unité systemd durcie ; Dockerfile minimal. **Non exécutés ici** (§10). |

---

## 6. Points ouverts (non corrigés)

| # | Gravité | Constat | Recommandation |
|---|---|---|---|
| O1 | Moyenne | **Aucune authentification ni TLS dans l'application** (le jeton ne protège que les écritures). | Proxy inverse avec TLS et authentification avant tout partage réseau (documenté). Pour un usage durable : comptes ou SSO au proxy. |
| O2 | Faible | Deux ajouts simultanés de la **même adresse** peuvent tous deux réussir (vérification puis insertion, sans index unique). Improbable avec un seul administrateur. | Index unique partiel `ON printers(host) WHERE enabled=1`, avec traitement de l'erreur en `409`. |
| O3 | Faible | `printer_state` renvoie « OK » pour une imprimante **aveugle** (aucun niveau connu) côté API ; le tableau de bord la range correctement, pas un consommateur externe de l'API. | Ajouter un état explicite (par exemple `BLIND`) dans l'API. |
| O4 | Faible | Adresse modifiée pendant un cycle : les relevés de l'ancienne adresse sont rattachés à la fiche pour ce cycle. | Ignorer le résultat si `host` a changé depuis le chargement de la liste (comparaison avant application). |
| O5 | Faible | Pas de **version de schéma**. | `PRAGMA user_version` + table de migrations dès le premier changement non additif. |
| O6 | Faible | **Pas de tests côté interface** et pas de test de navigateur automatisé ; la validation visuelle repose sur les captures d'écran que tu m'as envoyées. | Vitest + Testing Library pour `ui.ts`/`FleetTable`, et un test Playwright du parcours ajouter / modifier / retirer. |
| O7 | Faible | Complexité élevée dans `load_printers`, `cmd_matrix`, `cmd_serve`, `run_cycle`. | Découper au prochain ajout fonctionnel. |
| O8 | Faible | Pas de route de **santé** dédiée pour une supervision externe ni pour `HEALTHCHECK` Docker. | `GET /api/health` : état de la collecte, âge du dernier cycle. |

---

## 7. Sécurité : synthèse

**Surfaces exposées** : API HTTP (lecture, écriture de la liste d'imprimantes), SNMP sortant, HTTP(S) sortant vers les HP, SMTP sortant, fichiers locaux (base, `.env`).

| Menace | Mesure | Reste |
|---|---|---|
| Fuite de secrets SNMP / SMTP | Variables d'environnement, `snmp.conf` 0600, jamais en argument ; validation des caractères | Fichier temporaire si arrêt par `SIGKILL` |
| Injection d'arguments (`snmpwalk -v3 …`) | Validation stricte des adresses, `--` géré par l'analyseur | : |
| Injection SQL | Requêtes paramétrées ; f-strings uniquement sur constantes/listes blanches | : |
| XSS | React échappe ; aucun `innerHTML` ; CSP `default-src 'none'` | : |
| CSRF sur les écritures | Contrôle `Origin`/`Host`, JSON obligatoire, aucune règle CORS | : |
| **Rebinding DNS** | **Garde `Host` (R3)** | Proxy inverse : déclarer `PRINTWATCH_ALLOWED_HOSTS` |
| Écriture non autorisée | Mode `open` (local), `token` (comparé en temps constant), `off` | **Pas de comptes** (O1) |
| XML hostile d'une imprimante | Taille, `DOCTYPE`/`ENTITY` refusés, durée, redirections | : |
| Déni de service par clics répétés | Un cycle par 15 s, jamais deux en parallèle | : |
| Analyse de réseau (scan) | Liste explicite, jamais de balayage | Prévenir le SOC avant le premier cycle |

`bandit` après correction : 0 problème de gravité haute ; 3 « moyens » (SQL construit sur constantes, faux positifs) et 1 « moyen » (XML d'un équipement, atténué) ; 2 « bas » (import de `subprocess` et de `xml`).

---

## 8. Performance : mesures

Banc d'essai : 21 imprimantes, 4 consommables chacune, 35 jours d'historique, requêtes `GET /api/status` répétées.

| Intervalle de collecte | Relevés en base | V11 | V12 |
|---|---|---|---|
| 30 min | 141 204 | 299 ms | 3 ms |
| 10 min | 423 444 | 1 002 ms | 3 ms |

Le calcul n'a pas disparu : il est **déplacé dans le collecteur, une fois par cycle** (de l'ordre de 0,3 à 1 s par cycle à ce volume), au lieu d'être refait à chaque requête de chaque navigateur ouvert.

Volumétrie de la base : une ligne d'historique par consommable et par cycle (environ 90 lignes par cycle pour ce parc). À 30 minutes, environ **1,6 million de lignes par an** ; à 10 minutes, environ **4,8 millions**. Recommandation : **15 à 30 minutes** en production.

---

## 9. Tests

| Module | Couverture V12 |
|---|---|
| `notify.py`, `sync.py`, `model.py`, `normalize.py`, `series.py`, `alerts.py`, `demo.py` | 100 % ou proche |
| `storage.py` | 98 % |
| `web.py` | 98 % |
| `collector.py` | 97 % |
| `ledm.py` | 92 % |
| `config.py` | 90 % (reste : validations SNMPv3) |
| `cli.py` | 78 % (reste : câblage de `serve`, signaux de `loop`, cas d'erreur d'`export`) |
| **Total** | **93 %** (1 667 instructions, 114 non couvertes) |

**Lacunes de couverture restantes** : le démarrage réel du serveur (`serve`) n'est vérifié que par les tests bout en bout manuels de cette revue, pas par la suite automatique ; aucune vérification automatique de l'interface.

---

## 10. Limites de cette revue

- **Matériel** : le comportement SNMP et HP n'a été vérifié que sur ce qui a été observé (Epson WF-C5890, WF-C879R, WF-C20750 ; HP M479 et M181fw) et par simulation. D'autres modèles peuvent présenter des particularités.
- **Déploiement** : `Dockerfile`, `docker-compose.yml`, l'unité systemd et le lanceur `demo.ps1` ont été **lus mais pas exécutés**.
- **SNMPv3 AES** sur équipement réel : non validé.
- **Interface** : relue pour sa logique et ses motifs à risque, **pas re-lue ligne à ligne** dans cette passe ; pas de navigateur disponible ici pour contrôler le rendu.
- **Scripts PowerShell d'audit** (`scripts/powershell/`) : balayage par motifs uniquement.
- Les outils automatiques ont des faux positifs (signalés comme tels) et ne remplacent pas l'essai sur le réseau réel.

---

## 11. Priorités recommandées

1. **Déployer la V12**, en particulier pour R1, qui s'aggrave avec le temps.
2. Placer le dashboard derrière un **proxy inverse avec TLS et authentification** avant tout partage (O1), en renseignant `PRINTWATCH_ALLOWED_HOSTS`.
3. Passer l'intervalle de collecte à **15–30 minutes** en production.
4. Configurer le **SMTP** (testé, mais jamais utilisé sur un serveur réel) et vérifier la réception d'un courriel de synthèse.
5. Valider **systemd** ou **Docker** sur la VM cible, et la **version SNMPv3** sur du matériel réel.
6. Ajouter `GET /api/health` (O8) puis les tests d'interface (O6).
