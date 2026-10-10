# Dossier Technique & Spécifications PoC : PrintWatch

> **Note de mise à jour (8 oct. 2026).** L'interface décrite plus bas (trois vues, grand graphique, heatmap, journal, tableau, mode sombre, Recharts) a été
> **remplacée par une page unique en cartes** à la demande de l'utilisateur ; voir la section « Interface » du `README.md` pour l'état actuel.
> Ajouts depuis : colonne `user` du CSV, collecte intégrée (`serve --collect`) avec battement de cœur (table `cycles`) et bandeaux d'état,
> complément HP via l'interface web (`ledm.py`), tolérance aux CSV Excel (`;`, ANSI), masquage des imprimantes retirées du CSV.
> Depuis : gestion des imprimantes depuis le dashboard (la base fait foi, le CSV n'amorce qu'une base vide), bouton de synchronisation,
> cartes compactes à barres. Voir README, sections « Interface » et « Gérer les imprimantes depuis le dashboard ».
> Les passages relatifs aux anciennes vues restent à réécrire.


**Supervision de parc d'imprimantes — suivi prédictif des consommables**

---

## Table des matières

1. [Contexte, enjeux et synthèse exécutive](#1-contexte-enjeux-et-synthèse-exécutive)
2. [Rapport d'audit & résultats des tests réels](#2-rapport-daudit--résultats-des-tests-réels-18-équipements)
3. [Scripts PowerShell natifs & automatisation](#3-scripts-powershell-natifs--automatisation)
4. [Architecture frontend & analyse des options](#4-architecture-frontend--analyse-des-options-modernes)
5. [Spécifications & composants du dashboard](#5-spécifications--composants-du-dashboard-printwatch)
6. [Recommandations de sécurité & actions pratiques](#6-recommandations-de-sécurité--actions-pratiques)
7. [Guide de déploiement et maintenance](#7-guide-de-déploiement-et-maintenance)
8. [Architecture technique du code](#8-architecture-technique-du-code)

---

## 1. Contexte, enjeux et synthèse exécutive

**PrintWatch** est une solution de supervision et de suivi prédictif des consommables (toners et encres) pour l'ensemble du parc d'impression d'entreprise.

**Objectifs :**

- Éliminer les interruptions de service dues à l'épuisement d'un toner
- Optimiser les stocks de cartouches
- Offrir une visibilité temps réel aux équipes IT

### Points clés validés lors des tests réels

| Point | Détail |
|---|---|
| **Zero Admin Requirement** | Aucune authentification administrateur (HTTP/HTTPS) n'est requise pour collecter les métriques d'encre. |
| **Protocole SNMP standard** | SNMP v1/v2c sur UDP/161 avec la communauté par défaut `public` répond immédiatement sur l'ensemble des imprimantes actives. SNMP v3 n'est pas nécessaire pour ce niveau de collecte en lecture seule, mais reste la **cible** pour la production (compte dédié `authPriv`, géré par le collecteur). |
| **Outillage d'audit autonome** | Collecte ponctuelle et génération de rapports 100 % natives via PowerShell (sockets UDP natifs) : aucune dépendance externe sur les postes clients. |
| **Collecteur continu** | Le collecteur Python PrintWatch (stdlib uniquement + binaire `snmpwalk` de net-snmp) historise les relevés dans SQLite, déclenche des alertes à états, détecte les remplacements de cartouche et estime la date de rupture. |
| **Interface « super-visuelle »** | Application **React 19 + TypeScript + Tailwind CSS 4 + Recharts 3**, compilée en statique et servie directement par le backend **FastAPI** (aucun Node.js en production). |

---

## 2. Rapport d'audit & résultats des tests réels (18 équipements)

L'audit réseau, réalisé en environnement réel sur la plage d'adresses IP de l'inventaire fourni, donne les résultats suivants.

### Synthèse du parc

| Indicateur | Valeur | Part |
|---|---|---|
| Imprimantes auditées | 18 | 100 % |
| Actives (PING OK & SNMP répond) | 12 | 66,7 % |
| Hors-ligne / inaccessibles (PING échec) | 6 | 33,3 % |

> **Note de cohérence** : le tableau détaillé ci-dessous recense **11 équipements actifs et 7 hors-ligne** (dont `198.51.100.10`, absente du tableau d'actions du §6) ; la synthèse ci-dessus annonce 12 et 6. À confirmer lors du prochain audit.
> **Adresse confirmée** : l'imprimante HP est bien `198.51.100.11` (c'est elle qui répond au ping et au SNMP). L'inventaire GLPI indique à tort `198.51.100.12` : à corriger à la source.
> Par ailleurs, les niveaux « ATTENTION » de l'audit (25 à 30 %) correspondent à un seuil d'attention de **30 %**, alors que la valeur par défaut de PrintWatch est de 15 % : le seuil se règle dans `config.toml` (`[thresholds]`).

### Tableau récapitulatif de l'audit

| Adresse IP | PING | SNMP | Niveau d'encre / toner | État opérationnel & action |
|---|---|---|---|---|
| 198.51.100.13 | OK | OK | 1 % | 🔴 **CRITIQUE** — Remplacement immédiat |
| 198.51.100.14 | OK | OK | 25 % | 🟠 **ATTENTION** — Seuil bas |
| 198.51.100.15 | OK | OK | 26 % | 🟠 **ATTENTION** — Seuil bas |
| 198.51.100.16 | OK | OK | 30 % | 🟠 **ATTENTION** — Seuil bas |
| 198.51.100.17 | OK | OK | 76 % | 🟢 OK — Nominal |
| 198.51.100.18 | OK | OK | 77 % | 🟢 OK — Nominal |
| 198.51.100.19 | OK | OK | 96 % | 🟢 OK — Nominal |
| 198.51.100.20 | OK | OK | 98 % | 🟢 OK — Nominal |
| 198.51.100.21 | OK | OK | 100 % | 🟢 OK — Nominal |
| 198.51.100.22 | OK | OK | Niveau non remonté (code 254, probablement −2 « inconnu ») | ⚪ À vérifier — l'imprimante ne communique pas son niveau |
| 198.51.100.11 | OK | OK | Niveau non remonté (code 254, probablement −2 « inconnu ») | ⚪ À vérifier — l'imprimante ne communique pas son niveau (HP) |
| 198.51.100.10 | ÉCHEC | Inaccessible | N/A | ⚫ **HORS-LIGNE** — Éteinte / câble déconnecté |
| 198.51.100.23 | ÉCHEC | Inaccessible | N/A | ⚫ **HORS-LIGNE** — Éteinte / câble déconnecté |
| 198.51.100.24 | ÉCHEC | Inaccessible | N/A | ⚫ **HORS-LIGNE** — Éteinte / câble déconnecté |
| 198.51.100.25 | ÉCHEC | Inaccessible | N/A | ⚫ **HORS-LIGNE** — Éteinte / câble déconnecté |
| 198.51.100.26 | ÉCHEC | Inaccessible | N/A | ⚫ **HORS-LIGNE** — Éteinte / câble déconnecté |
| 198.51.100.27 | ÉCHEC | Inaccessible | N/A | ⚫ **HORS-LIGNE** — Éteinte / câble déconnecté |
| 198.51.100.28 | ÉCHEC | Inaccessible | N/A | ⚫ **HORS-LIGNE** — Éteinte / câble déconnecté |

> **Note technique — RFC 3805 (Printer-MIB)** : le code brut `254` lu par le script PowerShell est l'octet de l'entier **−2** (0xFE). La RFC 3805 définit −2 comme **« inconnu »** (l'imprimante ne communique pas son niveau) et −3 comme « il en reste, quantité non précisée ». Le code `254` ne prouve donc **pas** que la cartouche est installée et fonctionnelle : il faut lire la valeur réelle avec `printwatch probe` (net-snmp renvoie l'entier signé, −2 ou −3, sans l'ambiguïté de l'octet). L'audit ne lit en outre que le **premier** consommable de chaque imprimante (OID `…43.11.1.1.9.1.1`) : les autres encres et la caisse de maintenance ne sont pas couvertes par ces résultats.

---

---

## 3. Scripts PowerShell natifs & automatisation

Ces scripts (dossier `scripts/powershell/`) permettent de tester, collecter et automatiser la surveillance **sans aucune dépendance logicielle supplémentaire**.

### Script 1 — Détection réseau et validation SNMP (socket UDP natif)

```powershell
# Liste des adresses IP du parc
$ips = @(
    "198.51.100.17", "198.51.100.19", "198.51.100.20", "198.51.100.10",
    "198.51.100.21", "198.51.100.13", "198.51.100.16", "198.51.100.15",
    "198.51.100.14", "198.51.100.18", "198.51.100.22", "198.51.100.11",
    "198.51.100.23", "198.51.100.24", "198.51.100.25", "198.51.100.26",
    "198.51.100.27", "198.51.100.28"
)

# Trame SNMP v1 GetRequest (sysDescr) avec communauté 'public'
$snmpPacket = [byte[]](
    0x30, 0x29, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70, 0x75, 0x62, 0x6c, 0x69, 0x63,
    0xa0, 0x1c, 0x02, 0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00, 0x02, 0x01,
    0x00, 0x30, 0x0e, 0x30, 0x0c, 0x06, 0x08, 0x2b, 0x06, 0x01, 0x02, 0x01, 0x01,
    0x01, 0x00, 0x05, 0x00
)

$results = foreach ($ip in $ips) {
    $ping = Test-Connection -ComputerName $ip -Count 1 -Quiet
    $snmpOK = $false

    if ($ping) {
        $client = New-Object System.Net.Sockets.UdpClient
        $client.Client.ReceiveTimeout = 1200
        try {
            $client.Connect($ip, 161)
            [void]$client.Send($snmpPacket, $snmpPacket.Length)
            $remoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
            $resp = $client.Receive([ref]$remoteEP)
            if ($resp.Length -gt 0) { $snmpOK = $true }
        } catch {
            $snmpOK = $false
        } finally {
            $client.Close()
        }
    }

    [PSCustomObject]@{
        IP_Adresse  = $ip
        PING        = if ($ping) { "OK" } else { "ÉCHEC" }
        SNMP_Status = if ($snmpOK) { "OK (Opérationnel)" } else { "Inaccessible / Éteinte" }
    }
}

$results | Format-Table -AutoSize
```

### Script 2 — Extraction des niveaux de toner et export CSV

```powershell
$ips = @(
    "198.51.100.17", "198.51.100.19", "198.51.100.20", "198.51.100.21",
    "198.51.100.13", "198.51.100.16", "198.51.100.15", "198.51.100.14",
    "198.51.100.18", "198.51.100.22", "198.51.100.11"
)

# Trame OID 1.3.6.1.2.1.43.11.1.1.9.1.1 (prTonerLevel)
$snmpLevelReq = [byte[]](
    0x30, 0x2d, 0x02, 0x01, 0x00, 0x04, 0x06, 0x70, 0x75, 0x62, 0x6c, 0x69, 0x63,
    0xa0, 0x20, 0x02, 0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00, 0x02, 0x01,
    0x00, 0x30, 0x12, 0x30, 0x10, 0x06, 0x0c, 0x2b, 0x06, 0x01, 0x02, 0x01, 0x2b,
    0x0b, 0x01, 0x01, 0x09, 0x01, 0x01, 0x05, 0x00
)

$tonerReport = foreach ($ip in $ips) {
    $client = New-Object System.Net.Sockets.UdpClient
    $client.Client.ReceiveTimeout = 1500
    $tonerVal = "N/A"

    try {
        $client.Connect($ip, 161)
        [void]$client.Send($snmpLevelReq, $snmpLevelReq.Length)
        $remoteEP = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
        $resp = $client.Receive([ref]$remoteEP)
        if ($resp.Length -gt 5) {
            $lastByte = $resp[$resp.Length - 1]
            if ($lastByte -eq 254) {
                $tonerVal = "Niveau non remonté (254 = -2, inconnu)"
            } elseif ($lastByte -le 100 -and $lastByte -ge 0) {
                $tonerVal = "$lastByte %"
            } else {
                $tonerVal = "Code brut: $lastByte"
            }
        }
    } catch {
        $tonerVal = "Erreur lecture"
    } finally {
        $client.Close()
    }

    [PSCustomObject]@{
        IP_Adresse   = $ip
        Statut_SNMP  = "Actif"
        Niveau_Encre = $tonerVal
    }
}

$csvPath = "$env:USERPROFILE\Desktop\Rapport_Final_Encre.csv"
$tonerReport | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8 -Delimiter ";"
Write-Host "Rapport exporté avec succès sur le bureau : $csvPath" -ForegroundColor Green
```

### Script 3 — Automatisation via tâche planifiée Windows

Fichier : `scripts/powershell/03-Register-DailyTask.ps1`. La tâche relance le **fichier** `02-Export-Toner.ps1` (au lieu d'embarquer tout le script dans une ligne de commande) : on modifie la liste d'IP dans le script sans recréer la tâche.

```powershell
param([string]$At = '08:00')

$script = Join-Path $PSScriptRoot '02-Export-Toner.ps1'
if (-not (Test-Path $script)) { throw "Introuvable : $script" }

$action  = New-ScheduledTaskAction -Execute 'PowerShell.exe' `
           -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
Register-ScheduledTask -Action $action -Trigger $trigger -TaskName 'Rapport_Encre_Quotidien' `
    -Description "Mise à jour quotidienne automatique du rapport d'encre (PrintWatch)" -Force | Out-Null
Write-Host "Tâche « Rapport_Encre_Quotidien » enregistrée : tous les jours à $At" -ForegroundColor Green
```

> **Limite connue du Script 2** : il lit le dernier octet de la réponse SNMP. C'est exact pour les valeurs de 0 à 100 et pour `254` (octet de `-2`, soit « inconnu »), mais pas pour un entier codé sur plusieurs octets. Pour une collecte continue et fiable (SNMPv3, multi-marques, historique, alertes), utiliser le collecteur Python PrintWatch (§8).

---

## 4. Architecture frontend & analyse des options modernes

Pour répondre au besoin d'un dashboard ergonomique tout en respectant les contraintes de sécurité en entreprise, plusieurs architectures ont été comparées.

| Option | Avantages | Inconvénients | Verdict |
|---|---|---|---|
| **A. Page HTML/JS statique classique** | Zéro dépendance, aucun outil de build. | Visuel limité, pas de graphiques interactifs ni d'animations fluides. | ❌ Rejeté (plafond visuel insuffisant) |
| **B. Vite + React + TS (servi par FastAPI)** | Rendu haut de gamme (Tailwind CSS, Recharts, Lucide Icons), aucun Node.js en production, bundle statique ultra-rapide. | Node.js requis uniquement sur le poste de développement pour générer le build. | ✅ **SÉLECTIONNÉ** (recommandé pour la production et le SOC) |
| **C1. Next.js en `output: 'export'`** | Rendu statique équivalent à l'option B, valorisation du CV Next.js. | Chaîne de build plus lourde et inutile, l'API étant gérée séparément par FastAPI. | ⚠️ Option secondaire acceptable |
| **C2. Next.js avec serveur Node.js (SSR)** | Server-Side Rendering dynamique. | Ajoute un second runtime serveur à sécuriser, complexifie le proxy et augmente la surface d'attaque. | ⛔ **DÉCONSEILLÉ** (incompatible avec les règles de sécurité du SOC) |

---

### Choix d'implémentation retenus

| Élément | Choix |
|---|---|
| Build | Vite 8, TypeScript strict, sortie directe dans `printwatch/static/` |
| UI | React 19, Tailwind CSS 4, jetons de design en variables CSS (thème clair et sombre) |
| Graphiques | Recharts 3 pour le grand graphique multi-séries et la courbe de détail ; mini-courbes (sparklines) en SVG pur sans dépendance ; éprouvettes, barres et heatmap en HTML/CSS |
| Navigation | Fragment d'URL (`#/`, `#/parc`, `#/journal`, `?p=<imprimante>&k=<consommable>` pour le tiroir) : aucun routeur externe, aucun repli côté serveur, le bouton « retour » ferme le tiroir et les vues sont partageables par lien |
| Typographie | Bricolage Grotesque (variable), **auto-hébergée** dans le bundle : aucune requête vers un CDN |
| Identité visuelle | Univers de l'impression : quadrichromie cyan / magenta / jaune / noir sur un papier gris-bleu ; les couleurs rouge, ambre et vert sont réservées aux états et toujours doublées d'une icône et d'un libellé |

---

## 5. Spécifications & composants du dashboard « PrintWatch »

### Principe : lisible en 3 secondes, le détail à la demande

L'écran d'origine empilait tout (titre, actions, barre de parc, cinq compteurs, classement, 18 cartes, graphique, journal : 2 855 px de haut). Il est remplacé par un dashboard de supervision à **trois vues** et un **tiroir de détail**. Trois mises en page ont été comparées : *console unique* (tout visible, mais dense d'emblée et peu lisible sur mobile), *mur de sparklines* (18 cartes de courbes : c'est le défaut d'origine, en version graphique) et **vue d'ensemble minimale + parc + tiroir**, retenue : première vue réduite à l'état global et aux actions, courbes au centre, détail au clic.

### Maquette fonctionnelle

```text
Vue d'ensemble | Parc (4) | Journal                          ● Mis à jour à 01:15   [ Mode sombre ]
+-----------------------------------------------+---------------------------------------------+
| (!) 1 imprimante en niveau critique           | À faire                                     |
|     3 autres demandent de l'attention         |  (!) Remplacer maintenant (1)    ≈ 2 j      |
| [!][▲][▲][▲][~][~][~][~][~][~][~][✓]…[✓]      |  (▲) Commander les cartouches (3)  ≈ 31 j   |
| [ 1 Critiques | 3 Attention | 7 Injoign. | 7 OK ]|  (~) Contrôler câble et alimentation (7)    |
+-----------------------------------------------+---------------------------------------------+
| Niveaux restants        [7 j|30 j|90 j] [Courbe|Tableau]                                      |
| ( Noir·SVC 1 % ) ( Noir·CdG 26 % ) … [+ Ajouter…]                                             |
|  100% ╲___                         │ Aujourd'hui                                              |
|   30% - - - - - - ╲_ _ _ _ _ _ _ _│ · · · · projection        courbes + seuils + réticule    |
|    5% - - - - - - - - - ╲_ _ _ _ _│                                                           |
+---------------------------------------+-----------------------------------------------------+
| Ruptures estimées (burn-down)         | Historique du parc : un carré par imprimante et jour |
|  ✖ Noir  SVC ●────────── ≈ 2 j        |  SVC   ▓▓▓▓▓▓▓▓▓▒▒▒▒▒▒▒▒▒▒▒▓▓▓▓▓▓▓  100 %           |
|  ▲ Noir  CdG ●────────── ≈ 26 j       |  Accueil ░░░░░░░░░░░░░░░░░░▒▒▒▒    91 %            |
+---------------------------------------+-----------------------------------------------------+

Parc : recherche + filtres par état + [Tableau|Cartes]
  État      Imprimante        Niveaux   Plus bas   Rupture   Tendance 30 j    Dernier relevé
  ✖ Critique SVC              ▮▮▮▮      1 % Noir   ≈ 2 j     ╲___            il y a 5 h
  ⌁ Injoignable Accueil     ▯         61 % périmé  —       - - - -         il y a 3 j
  Clic sur une ligne → tiroir latéral (éprouvettes animées, courbe du consommable, tendances,
  disponibilité 30 j, derniers événements)
```

### Fonctionnalités incluses dans la version actuelle

**Vue d'ensemble**
- **Verdict et tuiles du parc** : titre calculé selon l'état, une tuile par imprimante triée par urgence (forme + icône + couleur, hachures pour les injoignables), quatre compteurs animés qui ouvrent le parc filtré. Les catégories sont exclusives et leur somme vaut le nombre d'imprimantes.
- **À faire** : remplacer maintenant (critiques), commander (attention), contrôler câble et alimentation (injoignables, regroupées en une ligne). Chaque ligne ouvre le tiroir sur le consommable concerné.
- **Grand graphique multi-séries** : jusqu'à 8 consommables sur les mêmes axes, périodes **7, 30 et 90 jours**, survol à réticule avec infobulle triée par niveau, zones et lignes d'attention et de criticité aux **seuils de la configuration** (jamais codés en dur), **projection de rupture en pointillés** jusqu'à 0 %, marqueurs de **remplacement de cartouche**, séries activables, masquables et ajoutables (survol d'une pastille : les autres courbes s'atténuent). Par défaut : les six consommables les plus proches de la rupture, deux au plus par imprimante. Vue **Tableau** équivalente.
- **Ruptures estimées (burn-down)** : les dix consommables les plus proches de la rupture sur un axe en jours, avec la confiance de la prévision dans le libellé accessible.
- **Historique du parc (heatmap)** : un carré par imprimante et par jour sur 14, 30 ou 90 jours (pire état de la journée), tendance quotidienne des imprimantes à traiter, disponibilité sur la période. Les états sont distingués par des **motifs** en plus des couleurs (rayures : critique, points : attention, hachures grises : injoignable, cadre pointillé : sans pourcentage). Vue **Tableau** équivalente (jours critiques, en attention, injoignable, disponibilité).

**Parc**
- **Tableau dense** : une ligne par imprimante (état, nom et emplacement, mini-éprouvettes, plus bas niveau, rupture estimée, sparkline 30 jours, dernier relevé), tri par en-tête (`aria-sort`), recherche libre (nom, emplacement, modèle, marque), filtres par état avec compteurs. Les imprimantes injoignables restent visibles mais discrètes : liseré gris, texte atténué, niveau marqué « périmé », courbe grisée en pointillés, aucune prévision.
- **Vue cartes** : éprouvettes animées aux couleurs de l'encre avec repères des seuils, rupture estimée sous chaque éprouvette, tendance 30 jours.

**Tiroir de détail** (clic sur une imprimante, une tuile, une ligne ou un point de l'historique)
- Éprouvettes cliquables (le consommable choisi alimente la courbe), courbe 7/30/90 jours avec seuils, projection et remplacements, petits multiples de tous les consommables, barre de disponibilité sur 30 jours avec les périodes de coupure en clair, derniers événements de l'imprimante.
- Clavier : focus déplacé dans le tiroir, `Tab` y reste, `Échap` le ferme et rend le focus à l'élément d'origine.

**Cas particuliers gérés à l'écran**

| Cas | Affichage |
|---|---|
| Niveau −3 (« il en reste », sans pourcentage) | Pastille « Cartouche présente », aucune courbe, aucune alerte |
| Niveau −2 (« inconnu », ex. code brut 254 de l'audit PowerShell) | « — », aucune courbe, aucune alerte (l'imprimante ne communique pas son niveau) |
| Imprimante injoignable | Valeurs marquées « périmé », courbe grisée, aucune prévision, durée depuis le dernier relevé |
| Boîtier de maintenance (se remplit) | « Plein à 93 % », barre et courbe de remplissage (elle monte), zones d'alerte en haut, seuils inversés (15/5 restants = 85/95 % pleins) |
| Remplacement de cartouche | Saut à 100 % repéré par un trait pointillé sur les courbes, projection recalculée depuis le nouveau segment |
| Mono-toner | Une seule éprouvette et une seule mini-courbe |

**Transverse**
- **Mode sombre / clair** : préférence système par défaut, bascule mémorisée ; jeux de couleurs distincts pour les séries (hors rouge, ambre, vert, réservés aux états).
- **Animations sobres** : remplissage des éprouvettes, tracé des courbes, apparition des lignes ; `prefers-reduced-motion` coupe toutes les animations (vérifié : 0 animation active).
- **Fiabilité d'affichage** : rafraîchissement toutes les 60 s ; en cas de perte de l'API, les dernières données restent affichées avec un bandeau d'erreur. Le titre de l'onglet indique le nombre d'imprimantes en alerte.
- **Accessibilité** : navigation clavier avec focus visible, lien d'évitement, libellés ARIA, **vue tableau équivalente pour chaque graphique**, aucun état porté par la seule couleur (icône, libellé ou motif), pas de jargon technique dans l'interface.
- **Responsive** : bureau en priorité, mobile sans défilement horizontal (vérifié à 390 px sur les quatre vues).

---

## 6. Recommandations de sécurité & actions pratiques

### Sécurité & conformité SOC

- **Architecture backend isolée** : l'API FastAPI interroge SQLite en lecture seule et se place derrière un proxy TLS authentifié.
- **Absence de runtime Node.js en production** : le frontend React compilé est servi en fichiers statiques par FastAPI via `/assets`, ce qui supprime les vulnérabilités liées à un serveur Node.js exposé (ex. : CVE liées au Server-Side Rendering).
- **Restriction CSP** : polices auto-hébergées, élimination complète des CDN externes. L'application envoie sur toutes les réponses :
  `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
  ainsi que `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` et `Referrer-Policy: no-referrer`. (`'unsafe-inline'` ne vaut que pour les attributs `style` posés par React et Recharts, jamais pour des scripts.)
- **Secrets SNMP** : jamais en ligne de commande. Écrits dans un `snmp.conf` temporaire (dossier 0700, fichier 0600), lus via `SNMPCONFPATH`, supprimés à la sortie ; le service web n'en détient aucun.
- **Interrogation d'une liste explicite d'adresses**, jamais de balayage de sous-réseau (un scan UDP/161 ressemble à de la reconnaissance pour un SOC). **Prévenir le SOC avant la première collecte.**
- **Aucune authentification ni TLS dans l'application** : écoute sur `127.0.0.1` par défaut. Pour un usage réel, placer un proxy inverse (nginx, Caddy, Traefik) avec TLS et authentification, et restreindre par adresse source.

### Actions sur le parc physique

| Priorité | Action | Équipements |
|---|---|---|
| 🔴 Immédiate | Commander et remplacer le toner (niveau restant : 1 %) | `198.51.100.13` |
| 🟠 Préventive | Mettre en commande les cartouches (seuil d'attention) | `198.51.100.14`, `198.51.100.15`, `198.51.100.16` |
| ⚫ Contrôle physique | Vérifier câbles Ethernet RJ45 et alimentation des 6 équipements injoignables | `198.51.100.23`, `198.51.100.24`, `.76`, `.74`, `.68`, `.66` |

---

---

## 7. Guide de déploiement et maintenance

### Essai local sur un poste Windows (sans snmpwalk ni Node.js)

Prérequis : Python 3.11 ou plus. Le frontend est déjà compilé dans l'archive.

```powershell
# 1. Extraction
Expand-Archive "$HOME\Downloads\printwatch.zip" -DestinationPath C:\dev -Force
cd C:\dev\printwatch

# 2. Environnement Python isolé
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass        # seulement si l'activation est bloquée
.\.venv\Scripts\Activate.ps1

# 3. Installation et tests unitaires (109 tests)
pip install -e ".[dev]"
pytest -q                                        # ou : .\tools\pwsync.ps1

# 4. Base de démonstration : parc de 18 imprimantes calqué sur l'audit
python tools\seed_demo.py data\demo.db --profile audit
@"
[snmp]
version = "2c"
[thresholds]
warn = 30.0
crit = 5.0
[collector]
db_path = "data/demo.db"
"@ | Set-Content demo.toml -Encoding ascii        # ascii : un BOM UTF-8 fait échouer la lecture du TOML

# 5. Lancement du serveur de supervision
printwatch -c demo.toml serve                    # http://127.0.0.1:8080
```

Le profil `audit` reprend les **noms, modèles et lieux réels** du parc (adresses fictives). La base de démonstration ne contacte aucune imprimante ; ses noms commencent par `DEMO` et ses adresses sont en `192.0.2.0/24` (RFC 5737). La garder séparée de la base réelle.

### Collecte réelle (VM Linux ou Docker)

Le collecteur appelle `snmpwalk` (paquet `snmp`), qui n'existe pas nativement sous Windows : utiliser une VM Linux, WSL ou Docker.

```bash
sudo apt install snmp
pip install -e ".[web]"
cp config.example.toml config.toml
cp printers.example.csv printers.csv        # id,name,host,brand,model,location,user,enabled
# ou utiliser le printers.csv fourni : les 18 imprimantes réelles (noms, modèles et lieux issus de l'inventaire ;
# ni noms d'utilisateurs ni numéros de série). Ce fichier est ignoré par git : il contient les adresses du parc.
cp .env.example .env && chmod 600 .env

set -a; . ./.env; set +a
printwatch probe 10.0.0.10                  # UNE imprimante, aucune écriture
printwatch once -p printers.csv             # un cycle complet (écrit la base, envoie les alertes)
printwatch -c config.toml serve             # dashboard

docker compose up -d --build                # alternative : services collector + web (web sur 127.0.0.1:8080)
```

Le service `collector` n'écoute aucun port et détient les secrets ; le service `web` n'a aucun secret et lit la base en `mode=ro`. Les deux tournent en `read_only`, `cap_drop: ALL` et `no-new-privileges`.

### Modification de l'interface frontend (environnement dev)

```bash
cd frontend
npm ci                  # installation avec vérification du lockfile
npm run dev             # http://localhost:5173, proxy /api vers 127.0.0.1:8080 (lancer `printwatch serve` en parallèle)
npm run build           # tsc --noEmit puis vite build
# Les fichiers générés sont écrits directement dans printwatch/static/ (aucune copie manuelle)
```

### Maintenance

- Tests : `pytest -q` (109 tests, dont la vérification du bundle statique, des en-têtes de sécurité et des endpoints d'agrégats).
- Rétention : `retention_days = 400` dans `config.toml` ; la purge est automatique.
- Seuils : `[thresholds]` (`warn`, `crit`, `hysteresis`) dans `config.toml` ; le dashboard les reprend pour tracer les zones d'alerte.
- Alertes par courriel : section `[smtp]` ; un seul récapitulatif par cycle, les événements restent en attente si le SMTP échoue.

---

## 8. Architecture technique du code

### Backend Python (`printwatch/`)

| Module | Rôle |
|---|---|
| `snmp.py` | Client net-snmp (`snmpwalk`), SNMP v1/v2c/v3, lecture de la Printer MIB (RFC 3805) |
| `normalize.py` | Valeurs brutes vers consommables normalisés (valeurs spéciales `-1`, `-2`, `-3`, pourcentage inversé pour les réceptacles) |
| `alerts.py` | Seuils, hystérésis, machine à états OK / WARN / CRIT |
| `collector.py` | Cycle de collecte parallèle, détection d'injoignabilité et de remplacement, génération des événements |
| `storage.py` | SQLite en mode WAL : `printers`, `supplies`, `samples`, `events` |
| `forecast.py` | Régression linéaire sur le dernier segment pour estimer la rupture, avec niveau de confiance |
| `series.py` | Agrégats de lecture du dashboard, fonctions pures sans accès base : rééchantillonnage des séries, détection des remplacements, périodes d'injoignabilité, synthèse quotidienne du parc. Les seuils sont passés en paramètre |
| `notify.py` | Notifications par journal ou SMTP |
| `web.py` | API FastAPI en lecture seule (SQLite `mode=ro`) et service du bundle statique, en-têtes de sécurité |
| `cli.py` | Commandes `probe`, `matrix`, `once`, `loop`, `status`, `events`, `serve` |

### Frontend (`frontend/src/`)

| Fichier | Rôle |
|---|---|
| `App.tsx` | Coque : barre supérieure, onglets, thème, interrogation toutes les 60 s (état, événements, séries 30 j), tiroir |
| `Overview.tsx` | Verdict, tuiles, compteurs, « À faire », assemblage de la vue d'ensemble |
| `MultiChart.tsx` | Grand graphique multi-séries (Recharts) : sélection des séries, seuils, projection, remplacements, vue tableau |
| `Burndown.tsx`, `Heatmap.tsx` | Ruptures estimées ; historique du parc jour par jour |
| `FleetTable.tsx` | Parc : tableau dense triable et filtrable, vue cartes |
| `Drawer.tsx`, `SupplyChart.tsx` | Tiroir de détail ; courbe d'un consommable (boîtier tracé en remplissage) |
| `Tubes.tsx`, `Sparkline.tsx` | Éprouvettes, mini-éprouvettes, barres ; mini-courbes SVG |
| `EventLog.tsx` | Journal par jour avec filtres |
| `api.ts`, `ui.ts`, `index.css` | Types et appels API ; utilitaires, routage par fragment, hooks d'animation ; jetons de design et composants |

### API

| Endpoint | Rôle |
|---|---|
| `GET /` | Page unique du dashboard (aucune ressource externe) |
| `GET /api/status` | Imprimantes triées par urgence, consommables, alerte, rupture estimée, synthèse |
| `GET /api/history/{imprimante}/{consommable}?days=30` | Série `{ts, pct}` (1 à 400 jours) |
| `GET /api/events?limit=30` | Derniers événements |
| `GET /api/series?days=30&points=60[&printer=id]` | **Toutes** les séries du parc en une requête (1 à 400 jours, 8 à 400 points) : dernier relevé de chaque seau, grille de temps commune, dates de remplacement détectées avec le seuil de la configuration. Remplace des dizaines d'appels `history` pour les sparklines |
| `GET /api/availability?days=30` | Périodes d'injoignabilité et disponibilité en % par imprimante, reconstruites depuis les événements `down` / `up` |
| `GET /api/fleet/daily?days=30` | État de chaque imprimante par jour (jours UTC) et totaux du parc, avec les seuils de la configuration : alimente la heatmap |

Les trois nouveaux endpoints sont en lecture seule, valident leurs paramètres (422 hors bornes), répondent 503 sans base et portent les mêmes en-têtes de sécurité que le reste de l'API. `create_app` accepte une horloge injectable (`clock`) pour des tests déterministes.

### Comportement

| Sujet | Règle |
|---|---|
| Seuils | `warn` 15 %, `crit` 5 % restants par défaut, hystérésis 5 points |
| Remplacement | Hausse d'au moins 20 points entre deux relevés : événement `replaced`, prévision remise à zéro |
| Injoignable | Alerte après 3 échecs consécutifs, événement `up` au retour ; aucune prévision (valeurs marquées périmées) |
| Valeurs spéciales | `-1` autre, `-2` inconnu, `-3` « il en reste » : aucun pourcentage ni alerte |
| Disponibilité | Pas de sondage dédié : périodes reconstruites depuis les événements ; précision = cycle de collecte, avec le retard des 3 échecs consécutifs au début d'une coupure. Une imprimante marquée injoignable sans événement conservé est comptée depuis son dernier relevé réussi |
| État d'un jour (heatmap) | `injoignable` si coupée plus de la moitié du temps écoulé ce jour-là, sinon niveau d'après le pourcentage restant le plus bas du jour (mêmes comparaisons `<=` que les alertes), sinon « sans pourcentage » |
| Démonstration | `tools/seed_demo.py` : profil `demo` (4 imprimantes) ou `audit` (18 imprimantes) |

Le détail des tests SNMPv3 sur simulateur (`snmpsim`) est dans le `README.md` du dépôt.
