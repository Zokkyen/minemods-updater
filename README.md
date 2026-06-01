# MineMods Updater

MineMods Updater est un logiciel desktop Windows pour verifier et appliquer des mises a jour de mods Minecraft en tenant compte de la version du jeu et du loader (Fabric, Forge, Quilt, NeoForge).

## Fonctionnalites

- Scan des mods `.jar` dans un dossier `mods`
- Detection automatique du contexte (loader + version Minecraft)
- Verification des updates via Modrinth et CurseForge
- Matching strict anti faux-positifs
- Page de confiance de matching (scores/candidats avant validation)
- Mode **dry-run** (simulation sans ecrire sur disque)
- Mise a jour selective ou globale
- Backup automatique des anciens mods en `.old`
- Changelog filtre (breaking/fix/performance/other)
- Export de rapport **JSON + CSV** (avant/apres operation)
- Profils multi-instances Minecraft

## Captures (a ajouter)

Ajoute tes captures dans un dossier `assets/` puis de-commente ces lignes:

```md
![Ecran principal](assets/main-window.png)
![Confiance matching](assets/matching-confidence.png)
![Rapport exporte](assets/report-export.png)
```

## Prerequis

- Windows 10/11
- Python 3.10+
- Connexion internet (API providers)
- Cle API CurseForge optionnelle (recommandee pour CurseForge)

## Lancement en developpement

```powershell
.\run_app.bat
```

## Build de l executable

```powershell
.\build_exe.bat
```

Sortie attendue:

- `dist/MineModsUpdater/MineModsUpdater.exe`

## Utilisation rapide

1. Choisir le dossier `mods`
2. Verifier la version Minecraft et le loader (ou cliquer sur Auto detect)
3. Activer les providers voulus (Modrinth/CurseForge)
4. Lancer `Scanner` puis `Verifier updates`
5. Consulter `Confiance matching` avant validation
6. Activer `Dry-run` pour simuler si besoin
7. Lancer la mise a jour (selection ou globale)
8. Exporter le rapport JSON/CSV

## Rapports exportes

Le rapport inclut:

- Contexte d execution (profil, loader, version, providers)
- Snapshot avant (versions locales + statuts + matching)
- Snapshot apres (updates appliquees/simulees + erreurs)

Formats:

- JSON: detail complet
- CSV: exploitable rapidement dans tableur

## Publication GitHub (suggestion)

1. Creer le repository
2. Committer le code source
3. Creer un tag (ex: `v1.0.0`)
4. Creer une GitHub Release
5. Joindre `MineModsUpdater.exe` en asset
6. Ajouter changelog + captures

## Roadmap

- Support du scan multi-dossiers
- Rollback automatique depuis `.old`
- Historique local des operations
- Verification de dependances entre mods
- Telemetrie locale optionnelle (statistiques anonymes)

## Noms alternatifs coherents (specialises Minecraft mods)

- MineMods Updater (actuel)
- ModPack Patcher
- BlockMods Sync
- CraftMods Pulse
- LoaderAware Mods

## Licence

A definir (ex: MIT).
