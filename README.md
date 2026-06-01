# MineMods Updater

MineMods Updater est un logiciel desktop Windows qui vérifie et applique les mises à jour de mods Minecraft en tenant compte de la version du jeu et du loader (Fabric, Forge, Quilt, NeoForge).

## Fonctionnalités

- Scan des mods `.jar` dans un dossier `mods`
- Scan automatique dès la sélection du dossier `mods`
- Détection automatique du contexte (loader + version Minecraft)
- Vérification des mises à jour via Modrinth et CurseForge
- Vérification accélérée: traitement parallèle + cache local court des checks récents
- Matching strict anti faux-positifs
- Mode **dry-run** (simulation sans écrire sur disque)
- Mise à jour sélective ou globale
- Actions rapides: tout cocher / tout décocher sur les lignes affichées
- Backup automatique des anciens mods en `.old`
- Changelog filtré (breaking/fix/performance/other)
- Tableau mods orienté productivité: tri colonnes + redimensionnement manuel + recherche texte
- Filtres clarifiés: un sélecteur État + un sélecteur Source
- Bouton direct pour ouvrir la page du mod associé (Modrinth / CurseForge)
- Logs repliables pour laisser plus de place à la liste des mods
- Export de rapport **JSON + CSV** (avant/après opération)

## Captures (à ajouter)

Ajoute tes captures dans un dossier `assets/`, puis dé-commente ces lignes:

```md
![Écran principal](assets/main-window.png)
![Filtres et sélection visible](assets/filters-selection.png)
![Rapport exporté](assets/report-export.png)
```

## Prérequis

- Windows 10/11
- Python 3.10+
- Connexion internet (APIs providers)
- Clé API CurseForge optionnelle (recommandée pour CurseForge)

## Lancement en développement

```powershell
.\run_app.bat
```

## Build de l'exécutable

```powershell
.\build_exe.bat
```

Sortie attendue:

- `dist/MineModsUpdater/MineModsUpdater.exe`

## Utilisation rapide

1. Choisir le dossier `mods`
2. Vérifier la version Minecraft et le loader (ou cliquer sur `Auto detect`)
3. Activer les providers voulus (Modrinth/CurseForge)
4. Le scan démarre automatiquement après sélection du dossier (sinon bouton `Scanner`)
5. Lancer `Vérifier les mises à jour`
6. Utiliser les filtres `État` et `Source`, puis `Tout cocher (affichés)` si besoin
7. Vérifier les détails d'un mod et ouvrir sa page provider depuis le panneau de droite
8. Activer `Dry-run` pour simuler si besoin
9. Lancer la mise à jour (sélection ou globale)
10. Exporter le rapport JSON/CSV

## Performance et réactivité

- Le check des mises à jour est parallélisé pour réduire le temps d'attente sur les gros modpacks.
- Un cache local court (TTL ~90s) évite de refaire les mêmes requêtes juste après un check.
- Le log affiche maintenant la durée totale, le nombre de cache hits/misses et le nombre de workers utilisés.
- Le mapping provider est réutilisé et auto-corrigé côté Modrinth en cas de mapping obsolète (fallback automatique).

Bonnes pratiques:

- Lancer un premier check complet après scan.
- Enchaîner les vérifications rapides ensuite (tu bénéficies du cache).
- Garder le mode strict activé pour limiter les faux-positifs quand les noms de mods sont ambigus.

## Rapports exportés

Le rapport inclut:

- Contexte d'exécution (loader, version, providers)
- Snapshot avant (versions locales + statuts + matching)
- Snapshot après (updates appliquées/simulées + erreurs)

Formats:

- JSON: détail complet
- CSV: exploitable rapidement dans un tableur

## Publication GitHub (suggestion)

1. Créer le repository
2. Committer le code source
3. Créer un tag (ex: `v1.0.0`)
4. Créer une GitHub Release
5. Joindre `MineModsUpdater.exe` en asset
6. Ajouter changelog + captures

## Roadmap

- Support du scan multi-dossiers
- Rollback automatique depuis `.old`
- Historique local des opérations
- Vérification de dépendances entre mods
- Télémétrie locale optionnelle (statistiques anonymes)

## Noms alternatifs cohérents (spécialisés Minecraft mods)

- MineMods Updater (actuel)
- ModPack Patcher
- BlockMods Sync
- CraftMods Pulse
- LoaderAware Mods

## Licence

À définir (ex: MIT).
