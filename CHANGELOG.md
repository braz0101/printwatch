# Journal des versions

## 0.13.0 — 23 octobre 2026

### Sécurité
- `users add/passwd --temp` : mot de passe provisoire **aléatoire** (16 caractères), affiché une seule fois. Avant : égal à l'adresse.
- Compte provisoire : seules `/api/auth/me`, `/api/auth/password` et `/api/auth/logout` répondent (`403 must_change` ailleurs) ; l'interface n'affiche que « Mon mot de passe ».
- Déconnexion : le jeton est révoqué côté serveur (empreinte SHA-256 dans `users.json`, purgée à échéance).
- En-têtes de sécurité appliqués aussi aux réponses `401`/`403`/`400` (ordre des middlewares).
- Rôle `lecture` : l'adresse IP des imprimantes n'est plus servie.
- Carnet des techniciens validé (pas de `<`, `>`, caractères de contrôle ; adresse vérifiée ; champs inconnus refusés) ; nom échappé dans le suivi GLPI.
- `GET /api/tickets` : `limite` bornée à 1–200.
- Script Windows : droits de `data\` et `.secrets.enc` restreints à SYSTEM et Administrateurs.

### Fonctionnalités
- **Journal d'audit** : table `audit`, onglet Comptes (filtres, pagination, export CSV), `GET /api/audit`, `GET /api/audit.csv`, `printwatch audit`. Jamais de secret.
- Détection « déposée depuis l'intranet » insensible aux accents, à la casse et aux entités HTML ; marqueurs réglables (`[glpi] marqueurs_intranet`).
- Onglet Matériel : compteur de pages et pages sur 30 jours.
- Écran mural : noms d'imprimantes sur deux lignes, sans troncature.

### Dépôt et déploiement
- Aucune donnée propre à l'entreprise : nom d'hôte, certificat, site, port et dossier passés en paramètres ou via `deploy/windows/site.local.psd1` (non versionné, modèle `site.example.psd1`).
- `deploy/windows/iis/web.config` remplacé par `web.config.template` (généré par le script).
- Exemples, tests et captures sur données fictives ; image de fond de connexion remplacée par un fond CSS.
- README réécrit ; 276 tests.

### Impact
- Après le premier passage du script Windows, lancer `printwatch users …` / `secret …` / `audit` depuis un **PowerShell administrateur**.
- Créer `deploy\windows\site.local.psd1` (ou passer `-HostName` / `-CertSubject`) avant de rejouer `admin-install.ps1`.
