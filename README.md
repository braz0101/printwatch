# PrintWatch

**Supervision des consommables d'un parc d'imprimantes réseau** : niveaux d'encre et de toner, boîtiers de maintenance, alertes,
prévision de rupture et tableau de bord web, à partir de SNMP (Printer MIB, RFC 3805).

> Version **0.12.0** (livraison « V12 », issue de la revue de code : voir `docs/REVUE_DE_CODE.md`). Cœur Python **sans dépendance** (bibliothèque standard) ; le tableau de bord ajoute FastAPI et
> uvicorn ; l'interface est une application React compilée et livrée avec le dépôt (**Node.js n'est pas nécessaire pour l'exécuter**).

---

## Sommaire

1. [Fonctionnalités](#1-fonctionnalités)
2. [Architecture](#2-architecture)
3. [Prérequis](#3-prérequis)
4. [Démarrage rapide](#4-démarrage-rapide)
5. [Essayer sans imprimante : le mode démonstration](#5-essayer-sans-imprimante--le-mode-démonstration)
6. [Configuration](#6-configuration)
7. [Ligne de commande](#7-ligne-de-commande)
8. [Le tableau de bord](#8-le-tableau-de-bord)
9. [Gérer les imprimantes](#9-gérer-les-imprimantes)
10. [La collecte en détail](#10-la-collecte-en-détail)
11. [Règles d'alerte et de calcul](#11-règles-dalerte-et-de-calcul)
12. [API HTTP](#12-api-http)
13. [Modèle de données](#13-modèle-de-données)
14. [Sécurité](#14-sécurité)
15. [Déploiement](#15-déploiement)
16. [Exploitation : sauvegarde, mise à jour, diagnostic](#16-exploitation--sauvegarde-mise-à-jour-diagnostic)
17. [Développement](#17-développement)
18. [Dépannage](#18-dépannage)
19. [Limites connues](#19-limites-connues)
20. [Historique des versions](#20-historique-des-versions)

---

## 1. Fonctionnalités

**Collecte**
- Lecture SNMP **v1, v2c et v3** (`snmpwalk` / `snmpget` de net-snmp) de la table des consommables, pour toutes les marques qui
  respectent la Printer MIB (Epson, HP, Canon, Ricoh, Brother, Xerox…).
- **Complément HP** : les LaserJet Pro répondent « inconnu » (`-2`) en SNMP pour les cartouches très basses ou non HP ; PrintWatch lit alors
  l'interface web de l'imprimante (LEDM) pour combler ces niveaux.
- Interrogation d'une **liste explicite** d'imprimantes, en parallèle, avec une relance unique des échecs dans le même cycle.
- **Santé de la collecte** : chaque cycle est enregistré ; le dashboard sait si la collecte tourne, est arrêtée ou n'obtient plus de réponse.

**Alertes et analyse**
- Alertes **à états** (avertissement, critique, résolu), avec hystérésis : pas de message à chaque cycle, pas de yo-yo autour d'un seuil.
- Détection du **remplacement de cartouche**, de l'**injoignabilité** (après N échecs) et du **retour en ligne**.
- **Prévision de rupture** par régression linéaire sur la consommation observée.
- Un seul **courriel de synthèse** par cycle (SMTP) ou, sans SMTP, une écriture dans les journaux.

**Tableau de bord**
- **Une seule page** : une carte compacte par imprimante, niveaux en barres colorées, état en pastille, tri par urgence, filtres, recherche.
- Volet de détail avec éprouvettes, prévision et derniers événements.
- **Gestion depuis le dashboard** : ajouter, modifier (utilisateur, emplacement, nom, adresse…), retirer une imprimante.
- Bouton **Synchroniser** : relevé immédiat de tout le parc.
- Bandeaux d'état explicites (collecte arrêtée, aucune réponse…), accessibilité (clavier, ARIA, `prefers-reduced-motion`).

**Exploitation**
- Import / export CSV tolérants aux exports Excel (UTF-8 ou ANSI ; séparateur `,`, `;` ou tabulation).
- **Mode démonstration** sans imprimante ni net-snmp (fonctionne sous Windows).
- Outils : sonde d'une imprimante (`probe`), matrice de compatibilité (`matrix`), diagnostic de l'installation (`tools/diagnostic.py`).

---

## 2. Architecture

```
 ┌─────────────┐  SNMP (UDP 161)         ┌──────────────────────────┐        ┌────────────────────────┐
 │ Imprimantes │ ◀────────────────────── │  Collecteur              │        │  API FastAPI + page    │
 │ (Epson, HP…)│  HTTP(S) LEDM (HP seul) │  cycle : lecture, alertes│        │  statique (React)      │
 └─────────────┘                         └────────────┬─────────────┘        └───────────▲────────────┘
                                                      │ écrit                            │ lit (mode=ro)
                                                      ▼                                  │ écrit : gestion
                                       ┌──────────────────────────────────┐              │ des imprimantes
                                       │  SQLite (mode WAL) : printers,   │──────────────┘
                                       │  supplies, samples, events, cycles│
                                       └──────────────────────────────────┘
                              alertes ──▶ SMTP (courriel de synthèse) ou journaux
```

| Composant | Fichier | Rôle |
|---|---|---|
| Client SNMP | `printwatch/snmp.py` | Appelle `snmpwalk` / `snmpget`. Les secrets passent par un `snmp.conf` temporaire (0600), jamais en ligne de commande |
| Normalisation | `printwatch/normalize.py` | Table brute → consommables (couleur, type, pourcentage, valeurs spéciales, réceptacles inversés) |
| Complément HP | `printwatch/ledm.py` | Lit `/DevMgmt/ConsumableConfigDyn.xml` et comble les niveaux SNMP inconnus |
| Alertes | `printwatch/alerts.py` | Machine à états OK / avertissement / critique avec hystérésis |
| Prévision | `printwatch/forecast.py` | Date de rupture estimée et indice de confiance |
| Collecteur | `printwatch/collector.py` | Un cycle : interrogation parallèle, relance, application des résultats, événements, notification |
| Synchronisation | `printwatch/sync.py` | Pont « dashboard → boucle de collecte » (un cycle à la demande, sans martelage) |
| Stockage | `printwatch/storage.py` | SQLite (WAL), migrations automatiques, gestion des fiches d'imprimantes |
| Notifications | `printwatch/notify.py` | Courriel SMTP ou journal |
| API et dashboard | `printwatch/web.py`, `printwatch/static/` | API JSON, routes de gestion, page unique |
| Démo | `printwatch/demo.py` | Parc fictif et relevés simulés |
| CLI | `printwatch/cli.py` | `probe`, `once`, `loop`, `serve`, `import`, `export`, `status`, `events`, `matrix` |
| Interface | `frontend/` | Source React 19 + TypeScript + Tailwind 4 ; build commité dans `printwatch/static/` |

**Principe clé : la base de données fait foi.** La liste des imprimantes vit dans SQLite et se modifie depuis le dashboard. Le fichier CSV
n'est qu'un moyen d'**amorcer** une base vide, d'**importer** et d'**exporter** (voir [§9](#9-gérer-les-imprimantes)).

---

## 3. Prérequis

| Élément | Détail |
|---|---|
| Python | **3.11 ou plus récent** |
| net-snmp | binaires `snmpwalk` et `snmpget` : `sudo apt install snmp` (Linux / WSL), `brew install net-snmp` (macOS). **Inutile en mode démo** |
| Réseau | UDP/161 du serveur vers les imprimantes ; HTTP(S) vers les HP pour le complément LEDM. Lecture seule |
| Extras Python | `web` (FastAPI, pydantic, uvicorn) pour le dashboard ; `dev` (tests) |
| Navigateur | Un navigateur récent (Chrome, Edge, Firefox, Safari) |

Le cœur (`once`, `loop`, `probe`, `status`…) n'a aucune dépendance Python. Les imprimantes doivent avoir **SNMP activé** et une communauté
(v1/v2c) ou un compte (v3) en lecture seule.

---

## 4. Démarrage rapide

```bash
# 1. Environnement
sudo apt install snmp                          # snmpwalk / snmpget
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[web]"                        # ou ".[dev]" pour lancer aussi les tests (pytest)
pytest -q                                      # attendu : 152 passed

# 2. Fichiers de travail
cp config.example.toml config.toml             # éditer : version SNMP, seuils, SMTP, intervalle
cp printers.example.csv printers.csv           # liste d'amorçage (voir §6.3)
cp .env.example .env && chmod 600 .env         # secrets : jamais versionnés
set -a; . ./.env; set +a                       # charge les variables dans le shell

# 3. Essayer UNE imprimante, sans rien écrire
printwatch probe 10.0.0.10

# 4. Dashboard + collecte dans un seul processus
printwatch serve --collect -p printers.csv     # http://127.0.0.1:8080
```

Premier test sans compte SNMPv3 : dans `config.toml`, `version = "2c"` (ou `"1"`) et `PRINTWATCH_SNMP_COMMUNITY=<communauté>` dans l'environnement.

Au premier lancement avec `--collect`, la base est vide : le CSV passé avec `-p` l'amorce, un cycle part aussitôt, puis un cycle toutes les
`interval_s` secondes. Ensuite, **la liste se gère depuis le dashboard** (le CSV n'est plus relu).

Autres modes de lancement :

```bash
printwatch once -p printers.csv       # un cycle puis arrêt (écrit la base, envoie les alertes)
printwatch loop -p printers.csv       # collecte seule, en continu (service « collector » de Docker)
printwatch serve                      # dashboard seul (lit la base alimentée par un autre processus)
```

---

## 5. Essayer sans imprimante : le mode démonstration

```bash
printwatch serve --demo                # http://127.0.0.1:8080
```

- **Parc fictif** de 11 imprimantes (noms et utilisateurs inventés) dont 2 injoignables, des HP aux niveaux partiels, des niveaux critiques.
- **Relevés simulés** : les niveaux baissent au fil des minutes (un cycle par minute) pour voir apparaître les alertes.
- **Aucune dépendance réseau** : ni net-snmp, ni imprimante. **Aucun fichier de configuration lu** (donc ni SMTP réel, ni base de production).
- Base **jetable** `demo.db` dans le dossier courant, recréée à chaque lancement.
- Tout fonctionne : Synchroniser, ajout, édition, retrait. Une adresse ajoutée en `10.9.x.x` ou en `.255` « ne répond pas » ; les autres obtiennent des
  niveaux synthétiques déterministes.

**Sous Windows** (PowerShell, Python 3.11+ requis ; le script indique `winget install -e --id Python.Python.3.12` s'il manque) :

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\printwatch-v11.zip" -DestinationPath "$env:USERPROFILE\printwatch-demo" -Force
cd "$env:USERPROFILE\printwatch-demo\printwatch"
powershell -ExecutionPolicy Bypass -File .\tools\demo.ps1 [-Port 8080]
```

Le script crée un environnement `.venv-demo`, installe les dépendances, ouvre le navigateur et lance la démo. Ctrl+C l'arrête.

---

## 6. Configuration

### 6.1 `config.toml`

Aucun secret dans ce fichier. Fichier par défaut : `config.toml` du dossier courant, sinon `-c chemin`.

| Section | Clé | Défaut | Rôle |
|---|---|---|---|
| `[snmp]` | `version` | `"3"` | `"1"`, `"2c"` ou `"3"`. Certains Epson (WF-C5890 observés) ne répondent qu'en v1 |
| | `user` | | Compte SNMPv3 (obligatoire en v3) |
| | `level` | `authPriv` | `noAuthNoPriv`, `authNoPriv`, `authPriv` |
| | `auth_proto`, `priv_proto` | `SHA`, `AES` | Protocoles v3 |
| | `context` | | Contexte v3 (inutile pour une imprimante ; réservé au simulateur) |
| | `timeout`, `retries` | `3.0`, `1` | Secondes par requête et relances SNMP |
| `[thresholds]` | `warn` | `15.0` | Seuil d'avertissement, en **% restant** |
| | `crit` | `5.0` | Seuil critique, en % restant |
| | `hysteresis` | `5.0` | Marge avant de considérer l'alerte résolue |
| `[smtp]` | `host` | `""` | Vide : les alertes vont dans les journaux (expéditeur par défaut : `printwatch@localhost`) |
| | `port`, `starttls` | `25`, `false` | Port **465** : TLS implicite (SMTPS). Autre port : connexion en clair, relevée par STARTTLS si `starttls = true` |
| | `user` | `""` | Si renseigné : mot de passe **obligatoire** dans `PRINTWATCH_SMTP_PASSWORD` (absent : l'envoi échoue avec un message explicite) |
| | `sender`, `recipients` | | Expéditeur et liste de destinataires |
| `[collector]` | `db_path` | `printwatch.db` | Base SQLite (le fichier d'exemple propose `data/printwatch.db`) |
| | `interval_s` | `1800` | Intervalle entre deux cycles (l'encre varie lentement) |
| | `workers` | `8` | Interrogations parallèles |
| | `down_after` | `3` | Échecs consécutifs avant l'alerte « injoignable » |
| | `replacement_jump` | `20.0` | Hausse (points) lue comme un remplacement de cartouche |
| | `retention_days` | `400` | Durée de conservation des relevés, cycles et événements notifiés |
| | `hp_ledm` | `true` | Combler les niveaux SNMP inconnus des HP via l'interface web |
| | `retry_delay_s` | `3.0` | Relance unique des échecs dans le même cycle (`0` = désactivé) |

Pour la pratique : **15 minutes à 30 minutes** entre cycles suffisent ; en dessous de 5 minutes, la base grossit vite (une ligne par consommable et
par cycle) sans gain d'information.

### 6.2 Variables d'environnement (secrets)

| Variable | Usage |
|---|---|
| `PRINTWATCH_SNMP_COMMUNITY` | Communauté SNMP v1 / v2c |
| `PRINTWATCH_SNMP_AUTH_PASS`, `PRINTWATCH_SNMP_PRIV_PASS` | Mots de passe SNMPv3 |
| `PRINTWATCH_SMTP_PASSWORD` | Mot de passe SMTP (si `user` renseigné) |
| `PRINTWATCH_ADMIN_TOKEN` | Code administrateur du dashboard (voir [§14](#14-sécurité)) |
| `PRINTWATCH_ALLOWED_HOSTS` | Noms d'hôte acceptés dans l'en-tête `Host`, séparés par des virgules (indispensable derrière un proxy inverse ; voir [§14](#14-sécurité)) |

Elles se placent dans `.env` (`chmod 600`, jamais versionné). Elles ne transitent **jamais** par la ligne de commande.

### 6.3 Liste d'imprimantes (CSV)

```csv
id,name,host,brand,model,location,user,enabled
imp-100-16,Pool Comptabilité,10.0.239.71,EPSON,WF-C879RDWF,Bâtiment Administration,Marie DUPONT,1
```

| Colonne | Obligatoire | Description |
|---|---|---|
| `id` | oui | Identifiant stable (convention : `imp-<3e octet>-<4e octet>`) |
| `host` | oui | Adresse IP ou nom d'hôte (`hôte:port` accepté, utile pour un simulateur) |
| `name` | non | Nom affiché |
| `brand`, `model` | non | Marque et modèle |
| `location` | non | Emplacement (bâtiment, étage, service) |
| `user` | non | Utilisateur de l'imprimante (alias de colonne : `utilisateur`) |
| `enabled` | non | `1`/`0`, `oui`/`non`, `vrai`/`faux` (booléens d'Excel en français) ; défaut `1`. **Toute autre valeur est refusée** (jamais de désactivation silencieuse sur une faute de frappe) |

Le chargeur accepte les **exports Excel** : UTF-8 (avec ou sans BOM) ou ANSI (cp1252), séparateur `,` `;` ou tabulation, en-têtes en majuscules ou avec
espaces. Les adresses sont validées (une adresse commençant par `-` serait lue comme option par `snmpwalk`) et **une adresse en double est refusée**.

---

## 7. Ligne de commande

```text
printwatch [-c CONFIG] [-v] [--version] <commande> [options]
```

| Commande | Options | Rôle |
|---|---|---|
| `probe HOTE` | | Lit **une** imprimante, **aucune écriture**. Première chose à lancer sur une nouvelle imprimante (`HOTE:PORT` accepté) |
| `once` | `-p CSV` | Un cycle de collecte puis arrêt. Code de sortie 1 si toutes les imprimantes ont échoué |
| `loop` | `-p CSV` | Collecte en continu (un cycle toutes les `interval_s`) |
| `serve` | `--host` `--port` `--collect` `-p CSV` `--demo` `--no-manage` | Dashboard (défaut `127.0.0.1:8080`). `--collect` ajoute la collecte et le bouton Synchroniser ; `--demo` : parc fictif ; `--no-manage` interdit les modifications |
| `status` | `--json` | État courant, **une ligne par consommable**, trié par niveau, avec rupture estimée (`--json` : tableau d'objets `printer`, `supply`, `pct`, `alert`, `down`, `forecast_days`…) |
| `events` | `-n LIMITE` | Journal des derniers événements |
| `import` | `-p CSV` `--overwrite` `--prune` | Importe un CSV dans la base (voir [§9](#9-gérer-les-imprimantes)) |
| `export` | `-o FICHIER` `--excel` | Exporte la liste (sauvegarde ; `--excel` : séparateur `;` + BOM pour Excel en français) |
| `matrix` | `-p CSV` `-o FICHIER` | Matrice de compatibilité par marque et modèle, **sans adresse IP** (joignable à un compte rendu). Sans `-p` : la liste de la base (celle du dashboard) |

`-p` n'est utilisé que pour **amorcer une base vide** (`once`, `loop`, `serve --collect`). `-v` active les journaux détaillés.

Exemples :

```bash
printwatch probe 10.0.0.10                                  # que remonte cette imprimante ?
printwatch -c config.toml once -p printers.csv              # un cycle complet
printwatch status                                           # une ligne par consommable, triée par niveau
printwatch status --json | jq '.[] | select(.alert=="CRIT") | {printer, supply, pct}'
printwatch events -n 20
printwatch export -o sauvegarde.csv --excel                 # liste des imprimantes, lisible dans Excel
```

---

## 8. Le tableau de bord

Application **React 19 + TypeScript + Tailwind 4**, servie en fichiers statiques par FastAPI. Thème clair, page unique, actualisation automatique toutes les 60 s
(les dernières données restent affichées si l'API devient injoignable).

### 8.1 Disposition

| Zone | Contenu |
|---|---|
| **En-tête** | Logo ; **« Dernier relevé il y a X min »** (heure du dernier cycle SNMP, pas celle de la page ; pastille verte, orange ou rouge) ; bouton **Synchroniser** (si disponible) |
| **Bandeaux** | Collecte à l'arrêt, jamais lancée, ou aucune imprimante n'a répondu ; confirmations d'actions |
| **Barre d'outils** | Recherche ; filtres **Toutes / Critiques / Attention / OK** (avec compteurs) ; bouton **Ajouter** (si la gestion est active) |
| **Parc** | Une carte par imprimante qui a des données |
| **Sans données** | Puce repliable en bas de page (voir ci-dessous) |
| **Volet de détail** | S'ouvre à droite au clic sur une carte |

### 8.2 La carte d'imprimante

```
 Pool Comptabilité                                  (✓)     ← nom, pastille d'état
 Marie DUPONT · Bâtiment Administration                     ← utilisateur · emplacement (modèle au survol)
 ▬▬▬▬▬▬▬▬▬▬▬  29 %   ▬▬▬▬▬▬▬▬▬▬▬  82 %                      ← barres colorées par encre, pourcentage à droite
 ▬▬▬▬▬▬▬▬▬▬▬  17 %   ▬▬▬▬▬▬▬▬▬▬▬  22 %
```

- **Barres** : la couleur de la barre identifie l'encre (noir, cyan, magenta, jaune) ; pas de libellé. Le nom du consommable apparaît au survol.
  Une barre à l'état critique est cerclée de rouge ; le pourcentage est coloré selon l'alerte.
- **Pastille d'état** : icône et couleur ; le libellé (« Critique », « OK »…) apparaît au survol et pour les lecteurs d'écran.
- **« N non remontés »** (orange) : l'imprimante ne publie pas le niveau de N consommables (par exemple une cartouche HP non reconnue) ; **aucune alerte n'est
  possible sur ceux-là**.
- Les cartes sont **triées par urgence** (critique, attention, injoignable, OK), puis par niveau le plus bas.
- Clic n'importe où sur la carte : ouvre le volet de détail.

### 8.3 États

| État | Condition |
|---|---|
| **Critique** | Au moins un consommable sous le seuil critique (`crit`, 5 % par défaut) |
| **Attention** | Au moins un consommable sous le seuil d'avertissement (`warn`, 15 %) |
| **OK** | Aucune alerte |
| **Injoignable** | Aucune réponse depuis `down_after` cycles consécutifs ; les valeurs sont marquées périmées |
| **Sans relevé** | Aucun consommable lu pour l'instant |

Les états sont **exclusifs par imprimante** : la somme des pastilles du parc vaut le nombre d'imprimantes.

### 8.4 La section « Sans données »

Pour garder la page lisible, les imprimantes **sans données exploitables** (injoignables, jamais relevées, ou aux niveaux seulement partiels) sont repliées sous la puce
**« Sans données (N) »** en bas de page. Un clic la déroule : une tuile par imprimante avec son nom, son utilisateur, la raison (« relevé il y a 3 h »,
« jamais relevée », « niveaux non remontés ») et sa pastille. **Une imprimante en alerte n'est jamais masquée**, même si certains de ses niveaux manquent.
Une recherche porte toujours sur tout le parc, y compris cette section.

### 8.5 Le volet de détail

- En-tête : nom, emplacement · modèle, utilisateur, état.
- **Éprouvettes** de chaque consommable avec pourcentage et, si la prévision est disponible, **« ≈ N j »** avant rupture.
- Mention « Dernier relevé il y a X min » ; si l'imprimante ne répond plus, un message explique que les niveaux sont périmés.
- Ligne orange pour les niveaux non remontés.
- **Derniers événements** de l'imprimante (alertes, remplacements, pannes).
- **Crayon** : modifier la fiche ; retrait de la supervision ([§9](#9-gérer-les-imprimantes)).
- Fermeture : bouton ✕, clic hors du volet ou touche **Échap**. Le focus reste dans le volet (Tab / Maj+Tab) puis revient à la carte.

### 8.6 Bandeaux et pastille de l'en-tête

| État de la collecte | Pastille | Message |
|---|---|---|
| `ok` | verte | (aucun) |
| `stale` : aucun cycle depuis plus de 2 intervalles | rouge | « La collecte est à l'arrêt : dernier relevé il y a X » |
| `never` : aucun cycle enregistré | rouge | « Aucune collecte enregistrée » : lancer `serve --collect` ou `loop` |
| `all_failed` : le dernier cycle n'a obtenu aucune réponse | orange | « Aucune imprimante n'a répondu au dernier relevé » : réseau, communauté, identifiants |
| API injoignable | rouge | « Connexion perdue » ; les dernières données restent affichées |

### 8.7 Affichage des noms

Les abréviations courantes sont **développées à l'affichage** (les données restent intactes) : `BAT` → Bâtiment, `ADM` → Administration, `INSP` → Inspection,
`RDC` → Rez-de-chaussée, `APPRO` → approvisionnement, `1IER` → 1er. Les mots en majuscules de plus de 3 lettres passent en minuscules avec majuscule initiale ;
les sigles de 3 lettres ou moins (RH, ADV…) sont conservés. Pour en ajouter : dictionnaire `ABBR` dans `frontend/src/ui.ts`, puis `npm run build`.

---

## 9. Gérer les imprimantes

### 9.1 Depuis le dashboard

| Action | Comment |
|---|---|
| **Ajouter** | Bouton **Ajouter** → nom et adresse obligatoires ; emplacement, utilisateur, marque, modèle facultatifs. L'identifiant est dérivé de l'adresse (`imp-<3e>-<4e octet>`). Un **premier relevé est lancé aussitôt**. Si l'imprimante ne répond pas en SNMP, elle apparaît sous « Sans données ». Une adresse déjà suivie est refusée (conflit) |
| **Modifier** | **Crayon** dans le volet de détail : nom, adresse, emplacement, utilisateur, marque, modèle. Seuls les champs changés sont envoyés. Un **changement d'adresse remet à zéro** l'état « injoignable » et déclenche un relevé |
| **Retirer** | Dans le formulaire de modification, **« Retirer de la supervision… »** avec confirmation. C'est un **archivage** : l'imprimante disparaît du dashboard, son **historique est conservé**. Ajouter de nouveau la même adresse la remet en service avec son historique |
| **Synchroniser** | Bouton en haut à droite : relevé immédiat de tout le parc. États : « Synchronisation… », « À jour », « Toujours en cours… » (au-delà de 90 s), « Échec, réessayer ». **Un cycle par 15 s au plus, jamais deux en parallèle** : un clic compulsif ne martèle pas le réseau. Disponible avec `serve --collect` |

Les boutons n'apparaissent que si le serveur les autorise (voir [§14](#14-sécurité)).

### 9.2 Import, export, sauvegarde

```bash
printwatch import -p printers.csv                 # ajoute les imprimantes inconnues ; ne touche PAS aux fiches existantes
printwatch import -p printers.csv --overwrite     # le CSV reprend la main sur les fiches existantes
printwatch import -p printers.csv --prune         # retire de la supervision celles absentes du CSV (historique conservé)
printwatch export -o sauvegarde.csv [--excel]     # liste complète (y compris archivées, colonne enabled)
```

**La base fait foi** : modifier `printers.csv` n'a aucun effet sur une base déjà peuplée, sauf `import --overwrite`. Le CSV amorce une base **vide** (une seule fois),
d'où la règle simple : *on édite dans le dashboard, on exporte pour sauvegarder, on importe pour migrer ou restaurer*.

---

## 10. La collecte en détail

### 10.1 Un cycle

1. **Liste** : lue dans la base à chaque cycle (les ajouts et modifications du dashboard sont pris en compte à chaud) ; base vide : amorçage depuis le CSV.
2. **Interrogation** : `snmpwalk` de la table des consommables (OID `1.3.6.1.2.1.43.11.1.1`, colonnes 4 à 9) et `snmpget` de la description système, en parallèle
   (`workers`). **HP** : si des toners restent « inconnus », lecture de l'interface web pour les combler.
3. **Relance** : les imprimantes en échec sont réinterrogées **une fois** après `retry_delay_s` (perte UDP ponctuelle, imprimante qui se réveille).
4. **Application** : mise à jour des consommables, un relevé en historique, calcul des états d'alerte, détection des remplacements, **calcul de la rupture estimée** (une seule fois par cycle, mémorisée en base), comptage des échecs.
5. **Notification** : un courriel de synthèse (ou une écriture dans les journaux).
6. **Nettoyage** : purge des données plus anciennes que `retention_days`, **au plus une fois par heure** (elle parcourt l'historique).
7. **Battement de cœur** : le cycle est enregistré (table `cycles`) : c'est ce que lit le dashboard pour afficher « Dernier relevé ».

### 10.2 Reprise après interruption

Si le dernier cycle date de plus de 2 intervalles (poste en veille, coupure de courant, service arrêté), le journal affiche *« collecte reprise après X min
d'interruption »* et **les échecs de ce cycle de reprise ne comptent pas vers « injoignable »** : le réseau ou les imprimantes sont peut-être encore en train de redémarrer.
Un échec persistant est bien compté au cycle suivant.

### 10.3 Complément HP (LEDM)

Les LaserJet Pro (M479 observés) répondent `-2` (« inconnu ») en SNMP pour les cartouches **très basses** ou **non HP** (compatibles), alors que leur interface web publie un état
(`ok`, `low`, `veryLow`, `nonHP`) et un pourcentage dans `/DevMgmt/ConsumableConfigDyn.xml`. Quand un toner reste inconnu en SNMP, PrintWatch lit ce fichier (HTTP, sinon HTTPS sans
vérification du certificat auto-signé de l'imprimante) et **comble uniquement les niveaux inconnus**, en faisant correspondre par référence de cartouche (par exemple `W2033A`), sinon par couleur.

- Une **valeur SNMP valide n'est jamais remplacée**.
- En cas d'échec (port fermé, XML inattendu, réponse trop volumineuse, `DOCTYPE`/`ENTITY` refusés), le niveau reste « inconnu » et le cycle continue.
- **Lecture du « 0 % »** : pour HP, `veryLow` signifie « très bas, à remplacer » : l'imprimante peut encore imprimer. L'alerte est critique, mais ce n'est pas forcément « vide ».
- Désactivation : `hp_ledm = false`.

### 10.4 Valeurs SNMP spéciales

| Valeur brute | Sens | Traitement |
|---|---|---|
| `-1` | autre | Aucun pourcentage, aucune alerte, état précédent conservé |
| `-2` | inconnu | Idem (affiché « — » ; comblé par LEDM pour les HP) |
| `-3` | « il en reste » | Aucun pourcentage ; « Présente » ; aucune alerte |

---

## 11. Règles d'alerte et de calcul

| Sujet | Règle |
|---|---|
| Pourcentage | `niveau / capacité`. **Restant** pour l'encre et le toner ; **inversé** pour les réceptacles (boîtier de maintenance, toner usagé) : « 7 % restant » = boîtier plein à 93 % |
| Seuils | `warn` 15 %, `crit` 5 % (restant), **hystérésis** de 5 points : l'alerte n'est levée qu'au-dessus de seuil + 5 |
| Événements | `warn`, `critical`, `resolved`, `replaced`, `down`, `up` |
| Premier relevé | Une cartouche déjà sous le seuil au démarrage déclenche son alerte |
| Remplacement | Hausse ≥ `replacement_jump` (20 points) entre deux relevés : état remis à zéro, événement `replaced` tracé (sans courriel) |
| Injoignable | Après `down_after` (3) échecs consécutifs : un événement `down` (une seule fois), puis `up` au retour. Les valeurs sont marquées périmées et aucune prévision n'est calculée |
| Courriel | **Un seul récapitulatif par cycle** ; si le SMTP échoue, les événements restent en attente et repartent au cycle suivant |
| Prévision | **Calculée par le collecteur à chaque cycle** (35 jours d'historique) et lue telle quelle par l'API (`/api/status` répond en quelques millisecondes quel que soit le volume d'historique). Régression linéaire sur le segment suivant le dernier remplacement ; confiance `low` / `medium` / `high` selon la durée et la chute observées ; consommation inférieure à 0,05 point/jour : « ne consomme pas » |
| Synthèse | Comptée **par imprimante** (critique, attention, injoignable, OK, sans relevé : exclusifs). Le courriel, lui, compte des **alertes** (consommables) |

---

## 12. API HTTP

Base : `http://127.0.0.1:8080`. Réponses JSON. (La documentation interactive de FastAPI est désactivée : la politique CSP du dashboard n'autorise aucune ressource externe.)

### 12.1 Lecture

| Méthode et route | Rôle |
|---|---|
| `GET /` | Page unique (HTML, CSS et JS locaux, aucune ressource externe) |
| `GET /api/status` | Imprimantes triées par urgence (consommables, alertes, prévision), résumé, seuils, **santé de la collecte** (`collector`) et **capacités** (`capabilities`) |
| `GET /api/events?limit=30` | Derniers événements |
| `GET /api/history/{imprimante}/{consommable}?days=30` | Série `{ts, pct}` d'un consommable |
| `GET /api/series?days=30&points=60[&printer=id]` | Séries de niveau du parc, rééchantillonnées sur une grille commune, avec les remplacements détectés |
| `GET /api/availability?days=30` | Périodes d'injoignabilité et disponibilité (%) par imprimante |
| `GET /api/fleet/daily?days=30` | État de chaque imprimante par jour et totaux du parc |

Les trois dernières routes ne sont plus utilisées par l'interface mais restent disponibles pour une intégration (rapports, supervision externe).

`/api/status` : champs utiles

```jsonc
{
  "generated_at": 1791500000.0,
  "collector": { "last_cycle": 1791499800.0, "polled": 21, "failed": 7, "interval_s": 1800,
                 "state": "ok",                       // ok | stale | never | all_failed
                 "running": false },                  // présent avec serve --collect
  "capabilities": { "sync": true, "manage": "open" }, // manage : off | open | token
  "summary": { "printers": 21, "crit": 4, "warn": 1, "down": 2, "ok": 5, "nodata": 9 },   // comptés par imprimante, exclusifs
  "printers": [ { "id": "imp-100-16", "name": "…", "state": "OK", "user": "…", "supplies": [ … ] } ]
}
```

L'adresse (`host`) d'une imprimante n'est exposée que si la gestion est active.

### 12.2 Écriture

| Méthode et route | Corps JSON | Réponses |
|---|---|---|
| `POST /api/sync` | (aucun) | `202` `{"state": "started" \| "running" \| "too_soon"}` ; `501` si la collecte n'est pas intégrée au serveur |
| `POST /api/printers` | `name`, `host` (obligatoires) ; `brand`, `model`, `location`, `user` | `201` fiche créée (`restored: true` si une imprimante archivée de même adresse est remise en service) ; `409` adresse déjà suivie ; `422` champ invalide |
| `PATCH /api/printers/{id}` | un ou plusieurs champs parmi `name`, `host`, `brand`, `model`, `location`, `user` | `200` fiche à jour ; `404` ; `409` ; `422` |
| `DELETE /api/printers/{id}` | | `200` `{"archived": true}` (historique conservé) ; `404` |

Garde-fous communs aux écritures : gestion active (`403` sinon), **même origine web** (`403` si l'en-tête `Origin` ne correspond pas à `Host`), code administrateur en mode `token`
(`401`), champs bornés (nom 80, adresse 253, emplacement 120, utilisateur 80, marque 40, modèle 60), caractères de contrôle refusés, champs inconnus refusés, adresse
validée.

```bash
# mode « open » (serveur local)
curl -s -XPOST localhost:8080/api/printers -H 'Content-Type: application/json' \
     -d '{"name":"Pool RH","host":"10.0.239.78","user":"Paul MARTIN","location":"BAT A"}'
curl -s -XPATCH localhost:8080/api/printers/imp-100-10 -H 'Content-Type: application/json' -d '{"user":"Claire FONTAINE"}'
curl -s -XDELETE localhost:8080/api/printers/imp-100-10
curl -s -XPOST localhost:8080/api/sync

# mode « token » : ajouter l'en-tête
curl -s -XPOST localhost:8080/api/printers -H "X-Admin-Token: $PRINTWATCH_ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{…}'
```

---

## 13. Modèle de données

SQLite en mode **WAL**. Le schéma est créé et **migré automatiquement** au démarrage (colonnes `user`, `fc_days`, `fc_conf`, table `cycles`). Un consommable que le dernier relevé réussi n'a **pas rafraîchi** (disparu de la table SNMP) est marqué **périmé** par l'API et n'entretient plus aucune alerte.

| Table | Contenu | Colonnes principales |
|---|---|---|
| `printers` | Une fiche par imprimante | `id`, `name`, `host`, `brand`, `model`, `location`, `user`, `enabled` (0 = archivée), `sys_descr`, `last_seen`, `last_error`, `failures`, `down` |
| `supplies` | État courant d'un consommable | `printer_id`, `key`, `description`, `kind`, `color`, `status`, `pct`, `level`, `max_capacity`, `alert_level`, `updated_at`, `fc_days`, `fc_conf` (rupture estimée) |
| `samples` | Historique des niveaux | `printer_id`, `key`, `ts`, `pct` (index sur `printer_id, key, ts`) |
| `events` | Événements d'alerte | `ts`, `printer_id`, `key`, `type`, `pct`, `message`, `notified` |
| `cycles` | Un enregistrement par cycle terminé | `ts`, `polled`, `failed`, `events`, `duration` |

Volumétrie : environ une ligne de `samples` par consommable et par cycle (≈ 90 lignes par cycle pour 21 imprimantes ; ≈ 1,6 million de lignes par an à 30 minutes). La purge
supprime les relevés, cycles et événements notifiés plus vieux que `retention_days`.

---

## 14. Sécurité

**Secrets**
- Les secrets SNMP ne passent **jamais en ligne de commande** : écrits dans un `snmp.conf` temporaire (dossier 0700, fichier 0600) lu via `SNMPCONFPATH`, supprimé à la sortie.
  Ils viennent de l'environnement, pas du TOML. `.env` ne se versionne pas.
- Cible : **SNMPv3 `authPriv`** (SHA / AES), compte dédié en lecture seule. v1 et v2c envoient la communauté en clair : usage temporaire.

**Réseau**
- Interrogation d'une **liste explicite** d'adresses, **jamais de balayage** de sous-réseau (un scan UDP/161 ressemble à de la reconnaissance pour un SOC). Prévenir le SOC avant le premier cycle.
- Flux UDP/161 du serveur vers les seules imprimantes, source fixe ; restreindre le SNMP sur chaque imprimante à l'adresse du serveur.
- `matrix` n'écrit aucune adresse IP : le fichier peut être joint à un compte rendu.

**Dashboard**
- En-têtes sur toutes les réponses : CSP `default-src 'none'` (scripts, polices, images et API en `'self'` uniquement), `X-Frame-Options: DENY`, `nosniff`, `no-referrer`.
  Polices auto-hébergées, aucun CDN. Textes d'origine SNMP affichés comme texte (jamais interprétés).
- **Lecture** : connexion SQLite ouverte en `mode=ro`. **Écriture** : routes de gestion dédiées, avec connexion courte distincte.
- **Gestion des imprimantes** (ajout, édition, retrait) :

| Situation | Mode | Effet |
|---|---|---|
| Écoute en local (`127.0.0.1`, défaut) | `open` | Gestion libre |
| `PRINTWATCH_ADMIN_TOKEN` défini | `token` | Le dashboard demande le code (une fois par onglet, `sessionStorage`) et l'envoie dans `X-Admin-Token` |
| Écoute hors boucle locale **sans** jeton | `off` | Gestion désactivée (avertissement au démarrage) |
| `--no-manage` | `off` | Gestion coupée quelle que soit la situation |
| Base en lecture seule (conteneur, volume monté en `ro`) | `off` | Gestion désactivée |

- Écritures refusées si elles viennent d'une autre origine web (anti-CSRF) ; entrées validées et bornées ; le jeton est comparé en temps constant.
- **Garde « Host » (anti rebinding DNS)** : en écoute locale, seuls `localhost`, `127.0.0.1` et `[::1]` sont acceptés dans l'en-tête `Host` (les autres reçoivent `400`). Sans elle, une page web malveillante dont le nom pointerait vers `127.0.0.1` atteindrait ce serveur avec une origine cohérente. Derrière un proxy inverse, déclarer le nom public : `PRINTWATCH_ALLOWED_HOSTS=printwatch.exemple.sn`. Hors boucle locale, la garde ne s'active que si cette variable est définie.
- **Le dashboard n'a pas de comptes utilisateurs ni de TLS.** Pour tout accès réseau : **proxy inverse** (TLS + authentification) devant, restriction par adresse source.
  Derrière un proxy sur la boucle locale, la gestion est « ouverte » à toute personne authentifiée par le proxy : ajouter un `PRINTWATCH_ADMIN_TOKEN` ou `--no-manage`.
- Le HTTPS vers les HP (complément LEDM) n'est pas vérifié (certificat auto-signé) : lecture seule, sans identifiants, sur le réseau interne. Les redirections vers un **autre hôte** sont refusées, la taille (256 Ko) et la **durée totale** de la réponse sont bornées, le XML est refusé s'il contient `DOCTYPE` ou `ENTITY`.

---

## 15. Déploiement

### 15.1 Poste de test (PoC)

```bash
printwatch serve --collect -p printers.csv
```

Un poste portable ou WSL **s'arrête en veille ou à la fermeture de la session** : le bandeau « La collecte est à l'arrêt » le signale. Sous Windows, désactiver la veille sur secteur :

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

### 15.2 VM Linux avec systemd (recommandé)

```bash
sudo useradd --system --home /opt/printwatch printwatch
sudo install -d -o printwatch /opt/printwatch/data
python3 -m venv /opt/printwatch/venv && /opt/printwatch/venv/bin/pip install "/chemin/vers/printwatch[web]"
# copier config.toml, printers.csv (amorçage) et .env (chmod 600) dans /opt/printwatch
sudo cp deploy/printwatch.service /etc/systemd/system/
sudo systemctl enable --now printwatch
journalctl -u printwatch -f
```

`deploy/printwatch.service` lance `serve --collect` sur `127.0.0.1:8080`, redémarre le service en cas d'arrêt et applique un durcissement (`NoNewPrivileges`, `ProtectSystem=strict`, écriture limitée à `data/`).

### 15.3 Docker Compose

```bash
mkdir -p data && sudo chown 10001 data         # utilisateur du conteneur
docker compose up -d --build
docker compose logs -f collector web
```

Deux services :

| Service | Rôle | Particularités |
|---|---|---|
| `collector` | Boucle de collecte (`loop`) | Seul à connaître les secrets SNMP et SMTP ; aucun port exposé ; système de fichiers en lecture seule, `cap_drop: ALL`, `no-new-privileges` |
| `web` | Dashboard (`serve`) | Aucun secret ; port publié sur `127.0.0.1:8080` seulement ; **lecture seule** : pas de bouton Synchroniser (le collecteur est un autre conteneur) |

Pour **modifier les imprimantes depuis le dashboard avec Docker**, décommenter `environment: ["PRINTWATCH_ADMIN_TOKEN"]` dans le service `web` et définir le jeton dans l'environnement de
`docker compose` (ne pas donner tout `.env` au service web : il contient les secrets SNMP). Le volume `data` doit rester accessible en écriture (mode WAL).
Dockerfile et `docker-compose.yml` n'ont **pas été exécutés** dans l'environnement de développement : à valider sur la VM.

### 15.4 Proxy inverse (TLS + authentification)

Exemple Caddy (TLS automatique, authentification de base) :

```caddyfile
printwatch.exemple.sn {
    basicauth {
        admin <hash généré par : caddy hash-password>
    }
    reverse_proxy 127.0.0.1:8080
}
```

nginx : transmettre l'en-tête `Host` d'origine (`proxy_set_header Host $host;`), sinon le contrôle d'origine des écritures refuse les requêtes du navigateur.

**Dans tous les cas**, déclarer le nom public auprès de PrintWatch (`PRINTWATCH_ALLOWED_HOSTS=printwatch.exemple.sn` dans `.env`) : sans cela, la garde « Host » répond `400` aux requêtes transmises par le proxy.

---

## 16. Exploitation : sauvegarde, mise à jour, diagnostic

### 16.1 Sauvegarde

```bash
# Copie cohérente de la base (sans arrêter le service)
python3 -c "import sqlite3; s=sqlite3.connect('data/printwatch.db'); d=sqlite3.connect('sauvegarde.db'); s.backup(d); d.close()"
# Liste des imprimantes, lisible et restaurable
printwatch export -o imprimantes.csv --excel
```

À planifier (cron, tâche planifiée). Restauration : remettre la base, ou `printwatch import -p imprimantes.csv --overwrite` sur une base vide.

### 16.2 Mise à jour

```bash
# arrêter le service, puis, depuis le dossier du projet :
rm -rf /tmp/pwnew && mkdir /tmp/pwnew && unzip -q printwatch-vXX.zip -d /tmp/pwnew
rsync -a --exclude=printers.csv --exclude=config.toml --exclude=.env --exclude=data/ \
      --exclude=__pycache__ --exclude='*.egg-info' /tmp/pwnew/printwatch/ ./
pip install -q -e ".[web]" && pytest -q       # tests : tous au vert
# relancer le service ; vider le cache du navigateur (Ctrl+Maj+R)
```

`config.toml`, `.env`, `printers.csv` et `data/` ne sont jamais écrasés. Les migrations de base sont automatiques.

### 16.3 Diagnostic de l'installation

```bash
python3 tools/diagnostic.py [data/printwatch.db] [printers.csv]    # lecture seule
```

Affiche : taille et mode de la base, âge du dernier cycle et interruptions récentes, état de chaque imprimante (échecs, dernier relevé, niveaux connus), écarts entre CSV et base,
adresses en double, volume de l'historique, événements non notifiés.

---

## 17. Développement

### 17.1 Tests

```bash
pip install -e ".[dev]"
pytest -q            # 152 tests, 93 % de couverture (ou : tools/pwsync / tools\pwsync.ps1 : installation + tests)
```

| Fichier | Couvre |
|---|---|
| `test_snmp.py`, `test_normalize.py` | Format réel de `snmpwalk`, secrets, hôtes, valeurs spéciales, réceptacles |
| `test_alerts.py`, `test_forecast.py` | Machine à états, hystérésis, prévision |
| `test_collector.py` | Cycle, injoignabilité, remplacement, complément HP, relance, reprise, battement de cœur |
| `test_ledm.py` | XML HP réels (`tests/fixtures/`), correspondance, refus d'entités, requête HTTP réelle locale |
| `test_loop.py` | La boucle survit aux erreurs |
| `test_review.py` | Correctifs de la revue : prévision persistée, consommables périmés, garde Host, file de synchronisation, CSV, SMTP, purge, durcissement LEDM, commandes de la CLI |
| `test_csv_formats.py` | Exports Excel : UTF-8/ANSI, `,` `;` tabulation |
| `test_web.py`, `test_web_series.py`, `test_static.py` | API de lecture, santé de la collecte, en-têtes, build statique |
| `test_management.py` | Ajout / édition / retrait, jeton, origine, validation, synchronisation, import / export |
| `test_demo.py`, `test_seed_demo.py` | Mode démo, base de démonstration |

### 17.2 Interface (`frontend/`)

```bash
cd frontend
npm ci                  # installe depuis package-lock.json
npm run dev             # http://localhost:5173, proxy /api → 127.0.0.1:8080 (lancer `printwatch serve` à côté)
npm run build           # type-check + build vers ../printwatch/static  (à COMMITER)
npm audit --omit=dev    # avant chaque livraison
```

Dépendances d'exécution : React, React DOM, une police auto-hébergée. Le build est **commité** : FastAPI ne sert que des fichiers statiques.

| Fichier | Rôle |
|---|---|
| `src/App.tsx` | Page, sondage de l'API, en-tête, bandeaux, synchronisation, panneaux |
| `src/FleetTable.tsx` | Filtres, recherche, cartes à barres, section « Sans données » |
| `src/Drawer.tsx`, `src/Tubes.tsx` | Volet de détail et éprouvettes |
| `src/PrinterForm.tsx`, `src/AddPanel.tsx` | Formulaire d'ajout et d'édition |
| `src/api.ts` | Types, client d'API, gestion du code administrateur |
| `src/ui.ts` | Libellés, couleurs, abréviations, routage par fragment d'URL |
| `src/EventLog.tsx`, `src/Icons.tsx`, `src/useDrawerFocus.ts`, `src/index.css` | Événements, icônes, focus des panneaux, styles |

### 17.3 Organisation du dépôt

```
printwatch/            paquet Python (cœur, API, démo)   frontend/        source React (build → printwatch/static)
tests/                 152 tests + fixtures HP           docs/            DOSSIER_TECHNIQUE.md (voir note de mise à jour)
tools/                 sonde, diagnostic, démo, simulateur snmpsim, base de démonstration
deploy/                service systemd                    scripts/powershell/   audit du parc sous Windows
```

### 17.4 Outils annexes

| Outil | Usage |
|---|---|
| `tools/demo.ps1` | Lance la démonstration sous Windows |
| `tools/diagnostic.py` | Diagnostic en lecture seule d'une installation |
| `tools/seed_demo.py` | Génère une base de démonstration avec 30 jours d'historique (`--profile audit` : parc de 18 imprimantes) |
| `tools/simulator/` | Imprimante virtuelle pour `snmpsim` : valider net-snmp → analyse → normalisation sans imprimante, y compris SNMPv3 |
| `scripts/powershell/` | `01-Test-Parc.ps1` (ping + SNMP), `02-Export-Toner.ps1` (CSV), `03-Register-DailyTask.ps1` (tâche quotidienne) |

Valider avec le vrai net-snmp, sans imprimante :

```bash
pip install snmpsim pysmi
snmpsim-command-responder --data-dir=tools/simulator --agent-udpv4-endpoint=127.0.0.1:1161 &
printf '[snmp]\nversion = "2c"\n' > config.toml && export PRINTWATCH_SNMP_COMMUNITY=printer-epson
printwatch probe 127.0.0.1:1161     # attendu : Black 62 %, Cyan 11 %, boîtier 7 % restant (93 % plein)
```

---

## 18. Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| Bandeau **« Aucune collecte enregistrée »** | Aucun collecteur n'a jamais tourné | Lancer `printwatch serve --collect -p printers.csv` (ou `loop`) |
| Bandeau **« La collecte est à l'arrêt »** | Processus arrêté, poste en veille, coupure | Relancer ; consulter les journaux ; `tools/diagnostic.py` (interruptions) ; désactiver la veille ; service systemd |
| Bandeau orange **« Aucune imprimante n'a répondu »** | Réseau, pare-feu, communauté ou identifiants SNMP | `printwatch probe <ip>` depuis le serveur ; vérifier la communauté et l'accès SNMP autorisé |
| Une imprimante **Injoignable** | Éteinte, déconnectée, IP changée, autre VLAN | `ping` ; lire l'adresse sur le panneau de l'imprimante ; corriger l'adresse (crayon) |
| `Timeout: No Response` alors que le `ping` répond | SNMP désactivé, mauvaise communauté, liste d'accès, **version** | Activer SNMP ; essayer `version = "1"` (certains Epson) ; vérifier la communauté |
| « **N non remontés** » sur une HP | Cartouche très basse ou non HP : SNMP répond `-2` | Laisser `hp_ledm = true` ; vérifier `curl -s http://<ip>/DevMgmt/ConsumableConfigDyn.xml` |
| Modifier `printers.csv` ne change rien | La base fait foi | Modifier depuis le dashboard, ou `printwatch import -p printers.csv --overwrite` |
| CSV exporté d'Excel illisible | (corrigé) | Les `;` et l'ANSI sont acceptés ; vérifier les noms de colonnes (`id`, `host` au minimum) |
| Boutons **Ajouter** / crayon absents, ou `403` | Gestion désactivée (serveur exposé sans jeton, `--no-manage`, base en lecture seule) | Définir `PRINTWATCH_ADMIN_TOKEN` ou écouter en local ; voir [§14](#14-sécurité) |
| `401` à l'enregistrement | Code administrateur incorrect | Saisir de nouveau le code (il est redemandé) |
| **Synchroniser** absent | Collecte non intégrée au serveur (Docker, `serve` seul) | Utiliser `serve --collect` ; en Docker, le prochain cycle du collecteur suffit |
| Ancien affichage après mise à jour | Cache du navigateur | Ctrl+Maj+R |
| `snmp.user est obligatoire en SNMPv3` | Configuration par défaut en v3 | Renseigner le compte v3, ou `version = "2c"` / `"1"` pour un test |
| Les alertes n'arrivent pas par courriel | `smtp.host` vide : les alertes vont dans les journaux | Renseigner `[smtp]` et `PRINTWATCH_SMTP_PASSWORD` |
| API : `503` base indisponible | La base n'existe pas encore | Lancer un cycle (`once`) ou `serve --collect` |
| Port 8080 déjà pris | Autre processus | `--port 8081` |
| `400 en-tête Host non autorisé` | Accès par un nom que la garde anti rebinding DNS ne connaît pas (proxy, nom DNS interne) | Définir `PRINTWATCH_ALLOWED_HOSTS` avec ce nom, puis relancer |
| Niveau « 0 % » sur une HP | État HP « très bas » (`veryLow`), pas forcément vide | Commander la cartouche ; voir [§10.3](#103-complément-hp-ledm) |

---

## 19. Limites connues

- **Aucune authentification ni TLS dans l'application** : à fournir par un proxy inverse.
- La valeur lue est celle que l'imprimante publie : certaines marques ne donnent que des paliers (25 / 50 / 75 / 100 %) ou seulement « il en reste » (`-3`). `matrix` sert à le savoir avant de promettre quoi que ce soit.
- **Complément web limité aux HP** (LEDM) ; pas de repli sur `prtAlertTable` pour les modèles qui ne publient aucun niveau.
- HP : « 0 % » signifie « très bas » ; deux sources (SNMP et web) peuvent différer de quelques points pour une même cartouche : SNMP prime quand il donne une valeur.
- SNMPv3 `authPriv` AES : validé avec le simulateur en `authNoPriv` et `authPriv` SHA+DES uniquement ; **AES et le chiffrement sur équipement réel restent à valider**.
- Dockerfile et `docker-compose.yml` non exécutés dans l'environnement de développement.
- Le collecteur intégré (`serve --collect`) dépend du processus : une veille ou un arrêt interrompt la collecte (le dashboard le signale).
- `printwatch once` lancé très rarement (par exemple une fois par jour) neutralise la tolérance de reprise : préférer `loop` ou `serve --collect`.
- **Revue de code complète** : voir `docs/REVUE_DE_CODE.md` (constats, corrections de la V12, points ouverts).
- Le dashboard retire une imprimante par **archivage** : pas de suppression définitive depuis l'interface (l'historique est conservé volontairement).
- Le rendu de l'interface a été conçu sur la base de captures d'écran de l'utilisateur ; aucun test de navigateur automatisé n'est inclus.
- `docs/DOSSIER_TECHNIQUE.md` décrit encore l'ancienne interface à plusieurs vues ; un bandeau l'indique, la réécriture reste à faire.

---

## 20. Historique des versions

| Version | Apports |
|---|---|
| **V1 – V5** | Refonte du front : page unique en cartes ; suppression des vues « Vue d'ensemble », « Journal », du tableau et du mode sombre ; imprimantes sans données repliées sous une puce ; volet de détail allégé ; **utilisateur** et abréviations développées (`BAT` → Bâtiment) ; nouvelle liste `printers.csv` avec colonne `user` |
| **V6** | **Collecte intégrée** (`serve --collect`), battement de cœur (table `cycles`), en-tête « Dernier relevé » exact, bandeaux d'état, **niveaux partiels** signalés |
| **V7** | **Complément HP** (LEDM) pour les niveaux SNMP inconnus, relance des échecs dans le cycle, tolérance après interruption |
| **V8** | Audit complet : CSV tolérant aux exports Excel (`;`, ANSI), correctif de documentation, outil `tools/diagnostic.py` |
| **V9** | **La base fait foi** ; **gestion depuis le dashboard** (ajouter, modifier, retirer) ; bouton **Synchroniser** ; `import` / `export` ; cartes compactes à barres ; sécurité des écritures (origine, jeton) |
| **V10** | **Mode démonstration** (`serve --demo`) et lanceur Windows `tools/demo.ps1` |
| **V11** | Cartes **épurées** (pastille d'état, barres sans libellé, une seule ligne secondaire, compteur supprimé) ; version `0.11.0` ; dépendance `pydantic` explicite ; ce README |
| **V12** | **Revue de code complète** (`docs/REVUE_DE_CODE.md`) : `/api/status` passe de 300 ms – 1 s à 5 ms (prévision calculée par le collecteur) ; consommables « fantômes » marqués périmés ; garde Host anti rebinding DNS ; synchronisation mise en file à l'ajout d'une imprimante ; CSV : `VRAI`/`FAUX`, valeurs invalides et adresses en double refusées ; SMTP 465 et mot de passe obligatoire ; durcissement de la lecture HP ; purge espacée ; `matrix` sur la base ; 152 tests, 93 % de couverture |
